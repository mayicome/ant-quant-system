#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
规则管理对话框
用于添加、编辑、删除交易规则
"""

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton, 
                             QListWidget, QListWidgetItem, QMessageBox, QLabel,
                             QFormLayout, QDoubleSpinBox, QSpinBox, QLineEdit,
                             QComboBox, QCheckBox, QGroupBox, QTimeEdit, QScrollArea,
                             QWidget, QSizePolicy)
from PyQt5.QtCore import Qt, pyqtSignal, QObject, QTime
import configparser
import os
from core.trading_rules import (RuleType, RULE_TYPE_NAMES, SingleBuyRule, SingleSellRule,
                                CageBuyRule, CageSellRule, BestSellRule, BestBuyRule,
                                GridBuyRule, GridSellRule, BreakthroughBuyRule, BreakthroughSellRule,
                                TradingRule)

class RecommendationUpdater(QObject):
    """用于在主线程中更新推荐值显示的辅助类"""
    update_signal = pyqtSignal()
    
    def __init__(self, dialog):
        super().__init__()
        self.dialog = dialog
    
    def update_display(self):
        """在主线程中更新显示"""
        if self.dialog and hasattr(self.dialog, '_update_recommendations_display'):
            try:
                if self.dialog.recommendation_service:
                    sell_rec = self.dialog.recommendation_service.get_sell_recommendations(self.dialog.stock_code)
                    buy_rec = self.dialog.recommendation_service.get_buy_recommendations(self.dialog.stock_code)
                    self.dialog._update_recommendations_display(sell_rec, buy_rec)
            except Exception as e:
                import traceback
                print(f"更新推荐值显示异常: {e}")
                print(traceback.format_exc())


class RulesManagerDialog(QDialog):
    """规则管理对话框"""
    
    def __init__(self, rules, stock_code, stock_name, parent=None):
        import time
        import json
        init_start = time.time()
        # #region agent log
        try:
            log_path = os.devnull
            log_entry = {
                'sessionId': 'debug-session',
                'runId': 'run1',
                'hypothesisId': 'C',
                'location': 'rules_manager_dialog.py:45',
                'message': 'RulesManagerDialog.__init__ started',
                'data': {'stock_code': stock_code, 'timestamp': int(time.time() * 1000)}
            }
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except: pass
        # #endregion
        
        super().__init__(parent)
        self.rules = rules.copy() if rules else []
        self.stock_code = stock_code
        self.stock_name = stock_name
        self.modified = False
        
        self.setWindowTitle(f"规则管理 - {stock_code} {stock_name}")
        self.setMinimumWidth(600)
        self.setMinimumHeight(500)
        
        # 创建推荐值更新器（用于在主线程中更新UI）
        self.recommendation_updater = RecommendationUpdater(self)
        self.recommendation_updater.update_signal.connect(self.recommendation_updater.update_display)
        
        # 获取推荐值服务
        try:
            from utils.recommendation_service import get_recommendation_service
            self.recommendation_service = get_recommendation_service()
        except Exception as e:
            import traceback
            print(f"获取推荐值服务失败: {e}")
            print(traceback.format_exc())
            self.recommendation_service = None
        
        setup_ui_start = time.time()
        self.setup_ui()
        setup_ui_time = time.time() - setup_ui_start
        # #region agent log
        try:
            log_path = os.devnull
            log_entry = {
                'sessionId': 'debug-session',
                'runId': 'run1',
                'hypothesisId': 'C',
                'location': 'rules_manager_dialog.py:70',
                'message': 'setup_ui completed',
                'data': {'stock_code': stock_code, 'setup_ui_time': setup_ui_time, 'timestamp': int(time.time() * 1000)}
            }
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except: pass
        # #endregion
        
        load_rules_start = time.time()
        self.load_rules()
        load_rules_time = time.time() - load_rules_start
        # #region agent log
        try:
            log_path = os.devnull
            log_entry = {
                'sessionId': 'debug-session',
                'runId': 'run1',
                'hypothesisId': 'C',
                'location': 'rules_manager_dialog.py:71',
                'message': 'load_rules completed',
                'data': {'stock_code': stock_code, 'load_rules_time': load_rules_time, 'timestamp': int(time.time() * 1000)}
            }
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except: pass
        # #endregion
        
        # 异步加载推荐值（如果还没有计算）
        try:
            load_rec_start = time.time()
            self._load_recommendations()
            load_rec_time = time.time() - load_rec_start
            # #region agent log
            try:
                log_path = os.devnull
                log_entry = {
                    'sessionId': 'debug-session',
                    'runId': 'run1',
                    'hypothesisId': 'C',
                    'location': 'rules_manager_dialog.py:75',
                    'message': '_load_recommendations completed',
                    'data': {'stock_code': stock_code, 'load_rec_time': load_rec_time, 'timestamp': int(time.time() * 1000)}
                }
                with open(log_path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
            except: pass
            # #endregion
        except Exception as e:
            import traceback
            print(f"加载推荐值失败: {e}")
            print(traceback.format_exc())
            # 即使加载推荐值失败，对话框也应该能正常打开
            if hasattr(self, 'recommendations_label'):
                self.recommendations_label.setText("推荐值加载失败")
                self.recommendations_label.setStyleSheet("color: #f44336; font-size: 11px; padding: 5px;")
        
        init_total_time = time.time() - init_start
        # #region agent log
        try:
            log_path = os.devnull
            log_entry = {
                'sessionId': 'debug-session',
                'runId': 'run1',
                'hypothesisId': 'C',
                'location': 'rules_manager_dialog.py:83',
                'message': 'RulesManagerDialog.__init__ completed',
                'data': {'stock_code': stock_code, 'init_total_time': init_total_time, 'timestamp': int(time.time() * 1000)}
            }
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
        except: pass
        # #endregion
    
    def setup_ui(self):
        """设置UI"""
        layout = QVBoxLayout(self)
        
        # 顶部标签
        title_label = QLabel(f"管理 {self.stock_code} ({self.stock_name}) 的交易规则")
        title_label.setStyleSheet("font-size: 14px; font-weight: bold; padding: 10px;")
        layout.addWidget(title_label)
        
        # 规则列表
        self.rules_list = QListWidget()
        self.rules_list.itemDoubleClicked.connect(self.edit_rule)
        layout.addWidget(self.rules_list)
        
        # 按钮区域
        btn_layout = QHBoxLayout()
        
        add_btn = QPushButton("➕ 添加规则")
        add_btn.clicked.connect(self.add_rule)
        btn_layout.addWidget(add_btn)
        
        edit_btn = QPushButton("✏️ 编辑规则")
        edit_btn.clicked.connect(self.edit_rule)
        btn_layout.addWidget(edit_btn)
        
        delete_btn = QPushButton("🗑️ 删除规则")
        delete_btn.clicked.connect(self.delete_rule)
        btn_layout.addWidget(delete_btn)
        
        clear_all_btn = QPushButton("🗑️ 清除所有规则")
        clear_all_btn.setStyleSheet("background-color: #f44336; color: white;")
        clear_all_btn.clicked.connect(self.clear_all_rules)
        btn_layout.addWidget(clear_all_btn)
        
        btn_layout.addStretch()
        
        layout.addLayout(btn_layout)
        
        # 推荐值显示区域
        self.recommendations_group = QGroupBox("推荐值（基于历史数据回测）")
        self.recommendations_layout = QVBoxLayout()
        self.recommendations_group.setLayout(self.recommendations_layout)
        
        # 推荐值标签（初始显示加载中）
        self.recommendations_label = QLabel("正在加载推荐值...")
        self.recommendations_label.setStyleSheet("color: #666; font-size: 11px; padding: 5px;")
        self.recommendations_layout.addWidget(self.recommendations_label)
        
        layout.addWidget(self.recommendations_group)
        
        # 底部按钮（合并为关闭按钮）
        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()
        
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)  # 规则修改是实时生效的，直接关闭即可
        bottom_layout.addWidget(close_btn)
        
        layout.addLayout(bottom_layout)
    
    def load_rules(self):
        """加载规则到列表"""
        self.rules_list.clear()
        for i, rule in enumerate(self.rules):
            rule_type = rule.get('type', '')
            rule_name = rule.get('name', '未命名规则')
            enabled = rule.get('enabled', True)
            
            # 获取规则类型的中文名称
            try:
                type_name = RULE_TYPE_NAMES.get(RuleType(rule_type), '未知类型') if rule_type else '未知类型'
            except (ValueError, AttributeError):
                # 处理未定义的规则类型（如 night_buy, night_sell, scheduled_clear 等）
                type_name_map = {
                    'night_buy': '夜市买入',
                    'night_sell': '夜市卖出',
                    'scheduled_clear': '定时清仓',
                }
                type_name = type_name_map.get(rule_type, '未知类型')
            
            # 根据规则类型显示详细信息
            detail = self._get_rule_detail(rule)
            
            # 创建显示文本
            status = "✓" if enabled else "✗"
            text = f"{status} [{type_name}] {rule_name} - {detail}"
            
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, i)  # 存储规则索引
            self.rules_list.addItem(item)
    
    def _get_rule_detail(self, rule):
        """获取规则详细信息"""
        rule_type = rule.get('type', '')
        
        if rule_type == 'single_buy':
            price = rule.get('price', 0)
            volume = rule.get('volume', 0)
            return f"价格{price:.2f}, {volume}股"
        
        elif rule_type == 'single_sell':
            price = rule.get('price', 0)
            volume = rule.get('volume', 0)
            detail = f"价格{price:.2f}, {volume}股"
            from core.rule_activation import rule_activation_detail_text
            act_text = rule_activation_detail_text(rule)
            if act_text:
                detail = f"{detail}, {act_text}"
            return detail
        
        elif rule_type == 'breakthrough_buy':
            price = rule.get('price', 0)
            volume = rule.get('volume', 0)
            if rule.get('band_low') is not None and rule.get('band_high') is not None:
                alo = rule.get('band_accept_low')
                alo_s = f", 有效下沿{float(alo):.2f}" if alo not in (None, "") else ""
                return (
                    f"价格带[{float(rule.get('band_low')):.2f},"
                    f"{float(rule.get('band_high')):.2f}]{alo_s}, {volume}股"
                )
            return f"突破价{price:.2f}, {volume}股（价格>突破价时买入）"
        
        elif rule_type == 'breakthrough_sell':
            price = rule.get('price', 0)
            volume = rule.get('volume', 0)
            detail = f"突破价{price:.2f}, {volume}股（价格<突破价时卖出）"
            from core.rule_activation import rule_activation_detail_text
            act_text = rule_activation_detail_text(rule)
            if act_text:
                detail = f"{detail}, {act_text}"
            return detail
        
        elif rule_type == 'cage_buy':
            price_low = rule.get('price_low', 0)
            price_high = rule.get('price_high', 0)
            volume = rule.get('volume', 0)
            return f"[{price_low:.2f} ~ {price_high:.2f}], {volume}股"
        
        elif rule_type == 'cage_sell':
            price_low = rule.get('price_low', 0)
            price_high = rule.get('price_high', 0)
            volume = rule.get('volume', 0)
            return f"[{price_low:.2f} ~ {price_high:.2f}], {volume}股"
        
        elif rule_type == 'best_sell':
            trigger_price = rule.get('trigger_price', 0)
            drop_percent = rule.get('drop_percent', 2.5)
            blend = rule.get('room_blend_start', '')
            volume = rule.get('volume', 0)
            vol_text = f"{volume}股" if volume > 0 else "全部"
            blend_text = f", 过渡{blend}pp" if blend not in ('', None) else ""
            return f"触发{trigger_price:.2f}, 回落{drop_percent}%{blend_text}, {vol_text}"
        
        elif rule_type == 'best_buy':
            trigger_price = rule.get('trigger_price', 0)
            rise_percent = rule.get('rise_percent', 0.3)
            volume = rule.get('volume', 0)
            return f"触发{trigger_price:.2f}, 反弹{rise_percent}%, {volume}股"
        
        elif rule_type == 'grid_buy':
            start_price = rule.get('start_price', 0)
            grid_step = rule.get('grid_step', 0.5)
            # 优先使用 volume_per_grid，如果没有则使用 volume
            volume = rule.get('volume_per_grid', rule.get('volume', 0))
            # 优先显示实际格数（num_grids），如果没有则显示最多格数（max_grids）
            num_grids = rule.get('num_grids', None)
            if num_grids is not None:
                return f"起始{start_price:.2f}, 间距{grid_step}, {volume}股/格, {num_grids}格"
            else:
                max_grids = rule.get('max_grids', 10)
                return f"起始{start_price:.2f}, 间距{grid_step}, {volume}股/格, 最多{max_grids}格"
        
        elif rule_type == 'grid_sell':
            start_price = rule.get('start_price', 0)
            grid_step = rule.get('grid_step', 0.5)
            # 优先使用 volume_per_grid，如果没有则使用 volume
            volume = rule.get('volume_per_grid', rule.get('volume', 0))
            # 优先显示实际格数（num_grids），如果没有则显示最多格数（max_grids）
            num_grids = rule.get('num_grids', None)
            if num_grids is not None:
                return f"起始{start_price:.2f}, 间距{grid_step}, {volume}股/格, {num_grids}格"
            else:
                max_grids = rule.get('max_grids', 10)
                return f"起始{start_price:.2f}, 间距{grid_step}, {volume}股/格, 最多{max_grids}格"
        
        elif rule_type == 'night_buy':
            price = rule.get('price', 0)
            volume = rule.get('volume', 0)
            return f"价格{price:.2f}, {volume}股（夜市委托）"
        
        elif rule_type == 'night_sell':
            price = rule.get('price', 0)
            volume = rule.get('volume', 0)
            return f"价格{price:.2f}, {volume}股（夜市委托）"
        
        elif rule_type == 'scheduled_clear':
            scheduled_clear_time = rule.get('scheduled_clear_time', '14:56:00')
            price = rule.get('price', 0)
            volume = rule.get('volume', 0)
            vol_text = f"{volume}股" if volume > 0 else "全部"
            return f"时间{scheduled_clear_time}, 触发价{price:.2f}, {vol_text}"
        
        return ""
    
    def add_rule(self):
        """添加规则"""
        dialog = AddRuleDialog(self, stock_code=self.stock_code)
        if dialog.exec_() == QDialog.Accepted:
            rule = dialog.get_rule()
            if rule:
                self.rules.append(rule)
                self.modified = True
                self.load_rules()
    
    def edit_rule(self):
        """编辑规则"""
        current_item = self.rules_list.currentItem()
        if not current_item:
            QMessageBox.warning(self, "提示", "请先选择要编辑的规则")
            return
        
        rule_index = current_item.data(Qt.UserRole)
        rule = self.rules[rule_index]
        
        dialog = AddRuleDialog(self, rule, stock_code=self.stock_code)
        if dialog.exec_() == QDialog.Accepted:
            updated_rule = dialog.get_rule()
            if updated_rule:
                self.rules[rule_index] = updated_rule
                self.modified = True
                self.load_rules()
    
    def delete_rule(self):
        """删除规则"""
        current_item = self.rules_list.currentItem()
        if not current_item:
            QMessageBox.warning(self, "提示", "请先选择要删除的规则")
            return
        
        reply = QMessageBox.question(
            self,
            "确认删除",
            "确定要删除选中的规则吗？",
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            rule_index = current_item.data(Qt.UserRole)
            del self.rules[rule_index]
            self.modified = True
            self.load_rules()
    
    def clear_all_rules(self):
        """清除所有规则"""
        if not self.rules:
            QMessageBox.information(self, "提示", "当前没有规则")
            return
        
        reply = QMessageBox.question(
            self,
            "⚠️ 确认清除",
            f"<b>警告：此操作将删除所有 {len(self.rules)} 条规则！</b><br><br>"
            f"确定要清除所有规则吗？此操作无法撤销。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            self.rules.clear()
            self.modified = True
            self.load_rules()
            QMessageBox.information(self, "完成", "已清除所有规则")
    
    def get_rules(self):
        """获取规则列表"""
        return self.rules
    
    def _load_require_manual_approval(self):
        """从config.ini加载是否需要人工审核设置，默认True"""
        try:
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(current_dir, 'data', 'config.ini')
            
            if os.path.exists(config_path):
                config = configparser.ConfigParser()
                config.read(config_path, encoding='utf-8')
                
                if 'Trading' in config:
                    value = config.get('Trading', 'require_manual_approval', fallback='1')
                    return value.lower() in ('1', 'true', 'yes', 'on')
            
            # 默认返回True（需要人工审核）
            return True
        except Exception as e:
            # 出错时默认返回True
            return True
    
    def _save_require_manual_approval(self, value):
        """保存是否需要人工审核设置到config.ini"""
        try:
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(current_dir, 'data', 'config.ini')
            
            # 确保data目录存在
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            
            config = configparser.ConfigParser()
            
            # 读取现有配置
            if os.path.exists(config_path):
                config.read(config_path, encoding='utf-8')
            
            # 添加或更新Trading节
            if 'Trading' not in config:
                config.add_section('Trading')
            
            # 保存设置（用1/0表示True/False）
            config.set('Trading', 'require_manual_approval', '1' if value else '0')
            
            # 写入文件
            with open(config_path, 'w', encoding='utf-8') as f:
                config.write(f)
            
            return True
        except Exception as e:
            QMessageBox.warning(self, "警告", f"保存配置失败: {str(e)}")
            return False
    
    def _load_early_order(self):
        """从config.ini加载是否提前下单设置，默认False"""
        try:
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(current_dir, 'data', 'config.ini')
            
            if os.path.exists(config_path):
                config = configparser.ConfigParser()
                config.read(config_path, encoding='utf-8')
                
                if 'Trading' in config:
                    value = config.get('Trading', 'early_order', fallback='0')
                    return value.lower() in ('1', 'true', 'yes', 'on')
            
            # 默认返回False（不提前下单）
            return False
        except Exception as e:
            # 出错时默认返回False
            return False
    
    def _save_early_order(self, value):
        """保存是否提前下单设置到config.ini"""
        try:
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(current_dir, 'data', 'config.ini')
            
            # 确保data目录存在
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            
            config = configparser.ConfigParser()
            
            # 读取现有配置
            if os.path.exists(config_path):
                config.read(config_path, encoding='utf-8')
            
            # 添加或更新Trading节
            if 'Trading' not in config:
                config.add_section('Trading')
            
            # 保存设置（用1/0表示True/False）
            config.set('Trading', 'early_order', '1' if value else '0')
            
            # 写入文件
            with open(config_path, 'w', encoding='utf-8') as f:
                config.write(f)
            
            return True
        except Exception as e:
            QMessageBox.warning(self, "警告", f"保存配置失败: {str(e)}")
            return False
    
    def accept(self):
        """保存设置并关闭对话框"""
        super().accept()
    
    def _load_recommendations(self):
        """加载推荐值"""
        if not self.recommendation_service:
            if hasattr(self, 'recommendations_label'):
                self.recommendations_label.setText("推荐值服务不可用")
                self.recommendations_label.setStyleSheet("color: #999; font-size: 11px; padding: 5px;")
            return
        
        try:
            # 先显示缓存的推荐值（如果有）
            sell_rec = self.recommendation_service.get_sell_recommendations(self.stock_code)
            buy_rec = self.recommendation_service.get_buy_recommendations(self.stock_code)
            
            if sell_rec or buy_rec:
                self._update_recommendations_display(sell_rec, buy_rec)
            else:
                # 如果没有缓存，标记为计算中
                if hasattr(self, 'recommendations_label'):
                    self.recommendations_label.setText("推荐值未计算，正在后台计算...")
                    self.recommendations_label.setStyleSheet("color: #ff9800; font-size: 11px; padding: 5px;")
            
            # 检查是否需要更新（超过1小时或不存在）
            from datetime import datetime, timedelta, date
            now = datetime.now()
            need_update = False
            
            def check_expired(rec, rec_type):
                """检查推荐值是否过期"""
                if not rec:
                    print(f"[推荐值检查] {self.stock_code} {rec_type}推荐不存在")
                    return True
                if rec.get('status') != 'calculated':
                    print(f"[推荐值检查] {self.stock_code} {rec_type}推荐状态={rec.get('status')}")
                    return True
                if not rec.get('last_update'):
                    print(f"[推荐值检查] {self.stock_code} {rec_type}推荐缺少last_update字段")
                    return True
                
                last_update = rec['last_update']
                print(f"[推荐值检查] {self.stock_code} {rec_type}推荐last_update原始值: {last_update}, 类型: {type(last_update)}")
                
                # 兼容旧数据：如果last_update是date类型（但不是datetime），转换为datetime
                # 注意：datetime是date的子类，所以要先检查datetime
                if isinstance(last_update, datetime):
                    # 已经是datetime类型，直接使用
                    pass
                elif isinstance(last_update, date):
                    # 是date类型但不是datetime，转换为datetime
                    last_update = datetime.combine(last_update, datetime.min.time())
                    print(f"[推荐值检查] {self.stock_code} {rec_type}推荐last_update从date转换为datetime: {last_update}")
                else:
                    print(f"[推荐值检查] {self.stock_code} {rec_type}推荐last_update类型错误: {type(last_update)}")
                    return True
                
                time_diff = now - last_update
                hours_diff = time_diff.total_seconds() / 3600
                print(f"[推荐值检查] {self.stock_code} {rec_type}推荐 now={now}, last_update={last_update}, 时间差={hours_diff:.2f}小时")
                if time_diff > timedelta(hours=1):
                    print(f"[推荐值检查] {self.stock_code} {rec_type}推荐已过期，时间差={hours_diff:.2f}小时")
                    return True
                else:
                    print(f"[推荐值检查] {self.stock_code} {rec_type}推荐有效，时间差={hours_diff:.2f}小时")
                    return False
            
            # 检查卖出和买入推荐是否过期
            sell_expired = check_expired(sell_rec, '卖出')
            buy_expired = check_expired(buy_rec, '买入')
            
            # 如果任一推荐值过期或不存在，都需要更新
            need_update = sell_expired or buy_expired
            
            # 如果需要更新，同时触发买入和卖出计算
            if need_update:
                # 同时计算买入和卖出推荐值，工作线程会自动共享tick数据
                self.recommendation_service.calculate_sell_recommendations_async(
                    self.stock_code,
                    days=3,
                    sample_interval=30,
                    callback=lambda code, rec: self._on_recommendations_updated('sell', rec)
                )
                self.recommendation_service.calculate_buy_recommendations_async(
                    self.stock_code,
                    days=3,
                    sample_interval=30,
                    callback=lambda code, rec: self._on_recommendations_updated('buy', rec)
                )
        except Exception as e:
            import traceback
            print(f"加载推荐值异常: {e}")
            print(traceback.format_exc())
            if hasattr(self, 'recommendations_label'):
                self.recommendations_label.setText(f"推荐值加载异常: {str(e)}")
                self.recommendations_label.setStyleSheet("color: #f44336; font-size: 11px; padding: 5px;")
    
    def _on_recommendations_updated(self, rec_type, recommendations):
        """推荐值计算完成后的回调（在工作线程中调用）"""
        try:
            # 检查对话框是否还存在（可能用户已经关闭了对话框）
            if not self.recommendation_service:
                return
            
            # 使用信号机制在主线程中更新UI（更可靠）
            if hasattr(self, 'recommendation_updater') and self.recommendation_updater:
                self.recommendation_updater.update_signal.emit()
        except Exception as e:
            import traceback
            print(f"更新推荐值显示异常: {e}")
            print(traceback.format_exc())
    
    def _update_recommendations_display(self, sell_rec, buy_rec):
        """更新推荐值显示"""
        try:
            text_parts = []
            is_calculating = False
            if self.recommendation_service:
                is_calculating = self.recommendation_service.is_calculating(self.stock_code)
            
            if sell_rec and sell_rec.get('status') == 'calculated':
                s1_drop = sell_rec.get('strategy1', 0)
                # 兼容旧数据：如果没有详细字段，使用默认值
                s1_sell_rate = sell_rec.get('strategy1_sell_rate', 0)
                s1_avg_profit = sell_rec.get('strategy1_avg_profit', 0)
                s1_max_drawdown = sell_rec.get('strategy1_max_drawdown', 0)
                
                s2_drop = sell_rec.get('strategy2', 0)
                s2_sell_rate = sell_rec.get('strategy2_sell_rate', 0)
                s2_avg_profit = sell_rec.get('strategy2_avg_profit', 0)
                s2_max_drawdown = sell_rec.get('strategy2_max_drawdown', 0)
                
                # 如果有详细数据，显示详细信息；否则只显示回落比例
                if s1_sell_rate > 0 or s2_sell_rate > 0:
                    text_parts.append(
                        f"<b>弹性卖出推荐：</b><br/>"
                        f"&nbsp;&nbsp;策略1：回落={s1_drop:.1f}% | 卖出率={s1_sell_rate:.0f}% | 平均收益={s1_avg_profit:.2f}% | 最大回撤={s1_max_drawdown:.2f}%<br/>"
                        f"&nbsp;&nbsp;策略2：回落={s2_drop:.1f}% | 卖出率={s2_sell_rate:.0f}% | 平均收益={s2_avg_profit:.2f}% | 最大回撤={s2_max_drawdown:.2f}%"
                    )
                else:
                    # 兼容旧数据格式
                    text_parts.append(f"<b>弹性卖出推荐：</b>策略1={s1_drop:.1f}% | 策略2={s2_drop:.1f}%")
            elif not sell_rec or sell_rec.get('status') != 'calculated':
                if is_calculating:
                    text_parts.append(f"<b>弹性卖出推荐：</b><span style='color: #ff9800;'>正在计算中...</span>")
                elif sell_rec and sell_rec.get('status') == 'error':
                    text_parts.append(f"<b>弹性卖出推荐：</b><span style='color: #f44336;'>计算失败</span>")
                else:
                    text_parts.append(f"<b>弹性卖出推荐：</b><span style='color: #999;'>未计算</span>")
            
            if buy_rec and buy_rec.get('status') == 'calculated':
                s1_rise = buy_rec.get('strategy1', 0)
                # 兼容旧数据：如果没有详细字段，使用默认值
                s1_buy_rate = buy_rec.get('strategy1_buy_rate', 0)
                s1_avg_saving = buy_rec.get('strategy1_avg_saving', 0)
                s1_max_drawdown = buy_rec.get('strategy1_max_drawdown', 0)
                
                s2_rise = buy_rec.get('strategy2', 0)
                s2_buy_rate = buy_rec.get('strategy2_buy_rate', 0)
                s2_avg_saving = buy_rec.get('strategy2_avg_saving', 0)
                s2_max_drawdown = buy_rec.get('strategy2_max_drawdown', 0)
                
                # 如果有详细数据，显示详细信息；否则只显示反弹比例
                if s1_buy_rate > 0 or s2_buy_rate > 0:
                    text_parts.append(
                        f"<b>弹性买入推荐：</b><br/>"
                        f"&nbsp;&nbsp;策略1：反弹={s1_rise:.1f}% | 买入率={s1_buy_rate:.0f}% | 平均节省={s1_avg_saving:.2f}% | 最大回撤={s1_max_drawdown:.2f}%<br/>"
                        f"&nbsp;&nbsp;策略2：反弹={s2_rise:.1f}% | 买入率={s2_buy_rate:.0f}% | 平均节省={s2_avg_saving:.2f}% | 最大回撤={s2_max_drawdown:.2f}%"
                    )
                else:
                    # 兼容旧数据格式
                    text_parts.append(f"<b>弹性买入推荐：</b>策略1={s1_rise:.1f}% | 策略2={s2_rise:.1f}%")
            elif not buy_rec or buy_rec.get('status') != 'calculated':
                if is_calculating:
                    text_parts.append(f"<b>弹性买入推荐：</b><span style='color: #ff9800;'>正在计算中...</span>")
                elif buy_rec and buy_rec.get('status') == 'error':
                    text_parts.append(f"<b>弹性买入推荐：</b><span style='color: #f44336;'>计算失败</span>")
                else:
                    text_parts.append(f"<b>弹性买入推荐：</b><span style='color: #999;'>未计算</span>")
            
            if text_parts:
                if hasattr(self, 'recommendations_label'):
                    self.recommendations_label.setText("<br>".join(text_parts))
                    if is_calculating:
                        self.recommendations_label.setStyleSheet("color: #ff9800; font-size: 11px; padding: 5px;")
                    else:
                        self.recommendations_label.setStyleSheet("color: #1976d2; font-size: 11px; padding: 5px;")
            else:
                if hasattr(self, 'recommendations_label'):
                    self.recommendations_label.setText("推荐值暂不可用")
                    self.recommendations_label.setStyleSheet("color: #999; font-size: 11px; padding: 5px;")
        except Exception as e:
            import traceback
            print(f"更新推荐值显示异常: {e}")
            print(traceback.format_exc())
            if hasattr(self, 'recommendations_label'):
                self.recommendations_label.setText(f"显示推荐值异常: {str(e)}")
                self.recommendations_label.setStyleSheet("color: #f44336; font-size: 11px; padding: 5px;")

class AddRuleDialog(QDialog):
    """添加/编辑规则对话框"""
    
    def __init__(self, parent=None, rule=None, stock_code=None):
        super().__init__(parent)
        self.rule = rule  # 如果是编辑模式，rule不为None
        self.is_edit_mode = rule is not None
        self.stock_code = stock_code  # 股票代码，用于获取推荐值
        
        self.setWindowTitle("编辑规则" if self.is_edit_mode else "添加规则")
        self.setMinimumWidth(480)
        self.setMinimumHeight(600)
        self.resize(500, 700)
        
        # 获取推荐值服务
        from utils.recommendation_service import get_recommendation_service
        self.recommendation_service = get_recommendation_service()
        
        self.setup_ui()
        
        if self.is_edit_mode:
            self.load_rule(rule)
    
    def setup_ui(self):
        """设置UI"""
        outer = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setFrameShape(QScrollArea.NoFrame)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(12)
        
        # 规则基本信息
        basic_group = QGroupBox("基本信息")
        basic_layout = QFormLayout()
        basic_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        
        # 规则名称
        self.name_edit = QLineEdit()
        basic_layout.addRow("规则名称:", self.name_edit)
        
        # 规则类型
        self.type_combo = QComboBox()
        for rule_type in RuleType:
            self.type_combo.addItem(RULE_TYPE_NAMES[rule_type], rule_type.value)
        self.type_combo.currentIndexChanged.connect(self.on_type_changed)
        # 如果是编辑模式，禁用规则类型下拉框（不允许修改规则类型）
        if self.is_edit_mode:
            self.type_combo.setEnabled(False)
            self.type_combo.setToolTip("编辑模式下不允许修改规则类型")
        basic_layout.addRow("规则类型:", self.type_combo)
        
        # 是否启用
        self.enabled_check = QCheckBox("启用此规则")
        self.enabled_check.setChecked(True)
        basic_layout.addRow("", self.enabled_check)
        
        basic_group.setLayout(basic_layout)
        basic_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        layout.addWidget(basic_group, 0, Qt.AlignTop)
        
        # 规则参数（根据类型动态显示 — 每次切换类型重建内部容器，避免控件残留叠层）
        self.params_group = QGroupBox("规则参数")
        self._params_group_vbox = QVBoxLayout(self.params_group)
        self._params_group_vbox.setContentsMargins(10, 12, 10, 10)
        self._params_group_vbox.setSpacing(0)
        self.params_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._params_host = None
        self.params_layout = None
        layout.addWidget(self.params_group, 0, Qt.AlignTop)

        self._build_activation_group()
        layout.addWidget(self.activation_group, 0, Qt.AlignTop)
        
        # 推荐值提示标签（用于显示弹性买入/卖出的推荐值）
        self.recommendation_label = QLabel()
        self.recommendation_label.setWordWrap(True)
        self.recommendation_label.hide()  # 初始隐藏
        layout.addWidget(self.recommendation_label, 0, Qt.AlignTop)

        layout.addStretch(1)

        scroll.setWidget(content)
        self._scroll_content = content
        outer.addWidget(scroll, 1)
        
        # 初始化参数控件字典
        self.param_widgets = {}
        
        # 初始显示第一个规则类型的参数
        self.on_type_changed(0)
        
        # 底部按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()
        
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self.accept)
        btn_layout.addWidget(ok_btn)
        
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)
        
        outer.addLayout(btn_layout)
    
    def _update_recommendation_hint(self, rule_type):
        """更新推荐值提示"""
        # 确保 recommendation_label 已创建
        if not hasattr(self, 'recommendation_label') or not self.recommendation_label:
            return
        
        if not self.stock_code:
            self.recommendation_label.hide()
            return
        
        if rule_type == 'best_sell':
            # 弹性卖出：显示回落比例推荐值
            sell_rec = self.recommendation_service.get_sell_recommendations(self.stock_code)
            if sell_rec and sell_rec.get('status') == 'calculated':
                s1 = sell_rec.get('strategy1', 0)
                s2 = sell_rec.get('strategy2', 0)
                self.recommendation_label.setText(
                    f"💡 <b>推荐回落比例：</b>策略1（最高收益）={s1:.1f}% | "
                    f"策略2（卖出率≥75%）={s2:.1f}%"
                )
                self.recommendation_label.show()
            elif self.recommendation_service.is_calculating(self.stock_code):
                self.recommendation_label.setText("💡 正在计算推荐回落比例，请稍候...")
                self.recommendation_label.setStyleSheet("color: #ff9800; font-size: 10px; padding: 5px; background-color: #fff3e0; border-radius: 3px;")
                self.recommendation_label.show()
            else:
                self.recommendation_label.hide()
        elif rule_type == 'best_buy':
            # 弹性买入：显示反弹比例推荐值
            buy_rec = self.recommendation_service.get_buy_recommendations(self.stock_code)
            if buy_rec and buy_rec.get('status') == 'calculated':
                s1 = buy_rec.get('strategy1', 0)
                s2 = buy_rec.get('strategy2', 0)
                self.recommendation_label.setText(
                    f"💡 <b>推荐反弹比例：</b>策略1（最高节省）={s1:.1f}% | "
                    f"策略2（买入率≥75%）={s2:.1f}%"
                )
                self.recommendation_label.show()
            elif self.recommendation_service.is_calculating(self.stock_code):
                self.recommendation_label.setText("💡 正在计算推荐反弹比例，请稍候...")
                self.recommendation_label.setStyleSheet("color: #ff9800; font-size: 10px; padding: 5px; background-color: #fff3e0; border-radius: 3px;")
                self.recommendation_label.show()
            else:
                self.recommendation_label.hide()
        else:
            self.recommendation_label.hide()
    
    def _reset_params_host(self):
        """销毁并重建规则参数表单容器，避免 deleteLater 残留导致叠层。"""
        if self._params_host is not None:
            self._params_host.hide()
            self._params_group_vbox.removeWidget(self._params_host)
            self._params_host.setParent(None)
            self._params_host.deleteLater()
        self._params_host = QWidget()
        self.params_layout = QFormLayout(self._params_host)
        self.params_layout.setContentsMargins(0, 0, 0, 0)
        self.params_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        self.params_layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.params_layout.setVerticalSpacing(10)
        self.params_layout.setHorizontalSpacing(12)
        self._params_host.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self._params_group_vbox.addWidget(self._params_host, 0, Qt.AlignTop)
        self.param_widgets = {}

    def on_type_changed(self, index):
        """规则类型改变"""
        self._reset_params_host()
        
        # 根据选中的规则类型添加对应的参数控件
        rule_type = self.type_combo.currentData()
        
        if rule_type in ['single_buy', 'single_sell']:
            # 单点买入/卖出：价格、数量
            price_spin = QDoubleSpinBox()
            price_spin.setRange(0.01, 99999.99)
            price_spin.setDecimals(2)
            price_spin.setValue(10.00)
            self.params_layout.addRow("价格:", price_spin)
            self.param_widgets['price'] = price_spin
            
            volume_spin = QSpinBox()
            volume_spin.setRange(0, 999999900)
            volume_spin.setSingleStep(100)
            volume_spin.setValue(100)
            self.params_layout.addRow("数量:", volume_spin)
            self.param_widgets['volume'] = volume_spin
        
        elif rule_type in ['breakthrough_buy', 'breakthrough_sell']:
            # 突破买入/卖出：价格、数量；买入可附加价格带硬pass
            price_spin = QDoubleSpinBox()
            price_spin.setRange(0.01, 99999.99)
            price_spin.setDecimals(2)
            price_spin.setValue(10.00)
            self.params_layout.addRow("价格/上沿:", price_spin)
            self.param_widgets['price'] = price_spin
            
            volume_spin = QSpinBox()
            volume_spin.setRange(0, 999999900)
            volume_spin.setSingleStep(100)
            volume_spin.setValue(100)
            self.params_layout.addRow("数量:", volume_spin)
            self.param_widgets['volume'] = volume_spin

            if rule_type == 'breakthrough_buy':
                from PyQt5.QtWidgets import QCheckBox

                band_chk = QCheckBox("启用价格带硬pass（监控带内真突破；深位或卖一>MA5作废）")
                self.params_layout.addRow("", band_chk)
                self.param_widgets['enable_price_band'] = band_chk

                band_low_spin = QDoubleSpinBox()
                band_low_spin.setRange(0.01, 99999.99)
                band_low_spin.setDecimals(2)
                band_low_spin.setValue(9.70)
                self.params_layout.addRow("监控下沿 band_low:", band_low_spin)
                self.param_widgets['band_low'] = band_low_spin

                band_high_spin = QDoubleSpinBox()
                band_high_spin.setRange(0.01, 99999.99)
                band_high_spin.setDecimals(2)
                band_high_spin.setValue(10.00)
                self.params_layout.addRow("监控上沿 band_high:", band_high_spin)
                self.param_widgets['band_high'] = band_high_spin

                accept_spin = QDoubleSpinBox()
                accept_spin.setRange(0.01, 99999.99)
                accept_spin.setDecimals(2)
                accept_spin.setValue(9.90)
                self.params_layout.addRow("有效下沿 band_accept_low:", accept_spin)
                self.param_widgets['band_accept_low'] = accept_spin

                win_spin = QSpinBox()
                win_spin.setRange(5, 300)
                win_spin.setValue(45)
                self.params_layout.addRow("真突破窗(秒):", win_spin)
                self.param_widgets['true_breakthrough_window_sec'] = win_spin

                def _sync_band(on=None):
                    enabled = band_chk.isChecked()
                    for w in (band_low_spin, band_high_spin, accept_spin, win_spin):
                        w.setEnabled(enabled)

                band_chk.toggled.connect(_sync_band)
                _sync_band()
        
        elif rule_type in ['cage_buy', 'cage_sell']:
            # 笼子买入/卖出：下限、上限、数量
            price_low_spin = QDoubleSpinBox()
            price_low_spin.setRange(0.01, 99999.99)
            price_low_spin.setDecimals(2)
            price_low_spin.setValue(9.00)
            self.params_layout.addRow("价格下限:", price_low_spin)
            self.param_widgets['price_low'] = price_low_spin
            
            price_high_spin = QDoubleSpinBox()
            price_high_spin.setRange(0.01, 99999.99)
            price_high_spin.setDecimals(2)
            price_high_spin.setValue(11.00)
            self.params_layout.addRow("价格上限:", price_high_spin)
            self.param_widgets['price_high'] = price_high_spin
            
            volume_spin = QSpinBox()
            volume_spin.setRange(0, 999999900)
            volume_spin.setSingleStep(100)
            volume_spin.setValue(100)
            self.params_layout.addRow("数量:", volume_spin)
            self.param_widgets['volume'] = volume_spin
        
        elif rule_type == 'best_sell':
            trigger_price_spin = QDoubleSpinBox()
            trigger_price_spin.setRange(0.01, 99999.99)
            trigger_price_spin.setDecimals(2)
            trigger_price_spin.setValue(12.00)
            self.params_layout.addRow("触发价格:", trigger_price_spin)
            self.param_widgets['trigger_price'] = trigger_price_spin
            
            drop_percent_spin = QDoubleSpinBox()
            drop_percent_spin.setRange(0.0, 50.0)
            drop_percent_spin.setDecimals(1)
            drop_percent_spin.setValue(2.5)
            drop_percent_spin.setToolTip("宽段允许从峰值回撤的百分比")
            self.params_layout.addRow("回落百分比:", drop_percent_spin)
            self.param_widgets['drop_percent'] = drop_percent_spin

            blend_spin = QDoubleSpinBox()
            blend_spin.setRange(0.5, 10.0)
            blend_spin.setDecimals(1)
            blend_spin.setSingleStep(0.1)
            blend_spin.setValue(3.0)
            blend_spin.setToolTip(
                "距涨停还剩几个百分点（相对昨收）时开始往近板收紧；"
                "≥此值仍用满回落%。小回撤档常用3.0，大回撤档常用1.5"
            )
            self.params_layout.addRow("过渡起点(pp):", blend_spin)
            self.param_widgets['room_blend_start'] = blend_spin
            
            volume_spin = QSpinBox()
            volume_spin.setRange(0, 999999900)
            volume_spin.setSingleStep(100)
            volume_spin.setValue(0)
            self.params_layout.addRow("数量(0=全部):", volume_spin)
            self.param_widgets['volume'] = volume_spin
        
        elif rule_type == 'best_buy':
            # 弹性买入：触发价格、反弹百分比、数量
            trigger_price_spin = QDoubleSpinBox()
            trigger_price_spin.setRange(0.01, 99999.99)
            trigger_price_spin.setDecimals(2)
            trigger_price_spin.setValue(9.00)
            self.params_layout.addRow("触发价格:", trigger_price_spin)
            self.param_widgets['trigger_price'] = trigger_price_spin
            
            rise_percent_spin = QDoubleSpinBox()
            rise_percent_spin.setRange(0.0, 50.0)
            rise_percent_spin.setDecimals(1)
            rise_percent_spin.setValue(0.3)
            rise_percent_spin.setToolTip("反弹百分比：0%表示价格不再下跌时立即买入，无需等待反弹")
            self.params_layout.addRow("反弹百分比:", rise_percent_spin)
            self.param_widgets['rise_percent'] = rise_percent_spin
            
            volume_spin = QSpinBox()
            volume_spin.setRange(0, 999999900)
            volume_spin.setSingleStep(100)
            volume_spin.setValue(100)
            self.params_layout.addRow("数量:", volume_spin)
            self.param_widgets['volume'] = volume_spin
        
        elif rule_type in ['grid_buy', 'grid_sell']:
            # 网格买入/卖出：起始价格、网格间距、每格数量、最大网格数
            start_price_spin = QDoubleSpinBox()
            start_price_spin.setRange(0.01, 99999.99)
            start_price_spin.setDecimals(2)
            start_price_spin.setValue(10.00)
            self.params_layout.addRow("起始价格:", start_price_spin)
            self.param_widgets['start_price'] = start_price_spin
            
            grid_step_spin = QDoubleSpinBox()
            grid_step_spin.setRange(0.01, 100.0)
            grid_step_spin.setDecimals(2)
            grid_step_spin.setValue(0.50)
            self.params_layout.addRow("网格间距:", grid_step_spin)
            self.param_widgets['grid_step'] = grid_step_spin
            
            volume_spin = QSpinBox()
            volume_spin.setRange(0, 999999900)
            volume_spin.setSingleStep(100)
            volume_spin.setValue(100)
            self.params_layout.addRow("每格数量:", volume_spin)
            self.param_widgets['volume'] = volume_spin
            
            # 实际格数（num_grids），如果没有则使用 max_grids 作为后备
            num_grids_spin = QSpinBox()
            num_grids_spin.setRange(1, 100)
            num_grids_spin.setValue(2)
            self.params_layout.addRow("实际格数:", num_grids_spin)
            self.param_widgets['num_grids'] = num_grids_spin
        
        for w in self.param_widgets.values():
            if isinstance(w, (QDoubleSpinBox, QSpinBox, QComboBox, QTimeEdit)):
                self._style_form_control(w)

        # 更新推荐值提示
        self._update_recommendation_hint(rule_type)
        self._update_activation_group_visibility(rule_type)

    def _style_form_control(self, widget):
        """统一表单控件高度，避免文字被裁切。"""
        widget.setMinimumHeight(28)
        if isinstance(widget, QComboBox):
            widget.setMinimumWidth(220)
        elif isinstance(widget, (QDoubleSpinBox, QSpinBox)):
            widget.setMinimumWidth(140)
        elif isinstance(widget, QTimeEdit):
            widget.setMinimumWidth(120)

    def _build_activation_group(self):
        """单点卖 / 突破卖：延迟激活（独立于规则参数区，避免嵌套 FormLayout 裁切）。"""
        self.activation_group = QGroupBox("延迟激活（可选）")
        outer = QVBoxLayout(self.activation_group)
        outer.setContentsMargins(10, 12, 10, 12)
        outer.setSpacing(8)

        act_layout = QFormLayout()
        act_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        act_layout.setVerticalSpacing(10)
        act_layout.setHorizontalSpacing(12)
        act_layout.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        act_enable = QCheckBox("启用延迟激活")
        act_enable.setChecked(False)
        act_layout.addRow("", act_enable)
        self._activation_widgets = {"activation_enabled": act_enable}

        activate_time = QTimeEdit()
        activate_time.setDisplayFormat("HH:mm:ss")
        activate_time.setTime(QTime(9, 50, 0))
        self._style_form_control(activate_time)
        act_layout.addRow("激活时间:", activate_time)
        self._activation_widgets["activation_activate_at"] = activate_time

        mode_combo = QComboBox()
        mode_combo.addItem("达到条件则不激活（否决）", "suppress_if_reached")
        mode_combo.addItem("达到条件才激活（必须）", "require_if_reached")
        self._style_form_control(mode_combo)
        act_layout.addRow("激活模式:", mode_combo)
        self._activation_widgets["activation_mode"] = mode_combo

        check_combo = QComboBox()
        check_combo.addItem("到过指定价位", "price")
        check_combo.addItem("曾封涨停板", "limit_sealed")
        self._style_form_control(check_combo)
        act_layout.addRow("观察条件:", check_combo)
        self._activation_widgets["activation_check"] = check_combo

        level_spin = QDoubleSpinBox()
        level_spin.setRange(0.0, 99999.99)
        level_spin.setDecimals(2)
        level_spin.setSpecialValueText("自动(涨停价)")
        level_spin.setValue(0.0)
        self._style_form_control(level_spin)
        act_layout.addRow("观察价位:", level_spin)
        self._activation_widgets["activation_level"] = level_spin

        outer.addLayout(act_layout)

        hint = QLabel("到激活时间前仅观察是否满足条件；到点后按模式决定是否启用本规则。不勾选=立即生效。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #666; font-size: 10px; padding-top: 4px;")
        hint.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        outer.addWidget(hint)

        self.activation_group.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.activation_group.hide()

    def _update_activation_group_visibility(self, rule_type: str):
        show = rule_type in ("single_sell", "breakthrough_sell")
        self.activation_group.setVisible(show)
        for key in list(self.param_widgets.keys()):
            if str(key).startswith("activation_"):
                self.param_widgets.pop(key, None)
        if show:
            for key, widget in self._activation_widgets.items():
                self.param_widgets[key] = widget
        self.params_group.updateGeometry()
        self.activation_group.updateGeometry()
        if hasattr(self, "_scroll_content"):
            self._scroll_content.adjustSize()
    
    def load_rule(self, rule):
        """加载规则数据到控件"""
        rule_type = rule.get('type', '')

        self.type_combo.blockSignals(True)
        index = self.type_combo.findData(rule_type)
        if index >= 0:
            self.type_combo.setCurrentIndex(index)
        self.type_combo.blockSignals(False)
        self.on_type_changed(index if index >= 0 else 0)

        # 设置规则名称
        self.name_edit.setText(rule.get('name', ''))
        
        # 设置启用状态
        self.enabled_check.setChecked(rule.get('enabled', True))
        
        # 设置参数值
        for param_name, widget in self.param_widgets.items():
            if param_name.startswith("activation_"):
                continue
            if param_name == "enable_price_band":
                continue
            if param_name in rule:
                if isinstance(widget, (QDoubleSpinBox, QSpinBox)):
                    widget.setValue(rule[param_name])

        if rule_type == "breakthrough_buy" and "enable_price_band" in self.param_widgets:
            has_band = rule.get("band_low") is not None and rule.get("band_high") is not None
            self.param_widgets["enable_price_band"].setChecked(bool(has_band))
        
        # 特殊处理：网格规则的实际格数（num_grids）
        # 如果规则中有 num_grids，使用它；否则使用 max_grids 作为后备
        if rule_type in ['grid_buy', 'grid_sell']:
            if 'num_grids' in self.param_widgets:
                num_grids_widget = self.param_widgets['num_grids']
                if 'num_grids' in rule:
                    num_grids_widget.setValue(rule['num_grids'])
                elif 'max_grids' in rule:
                    # 如果没有 num_grids，使用 max_grids 作为后备
                    num_grids_widget.setValue(rule['max_grids'])
        
        # 特殊处理：网格规则的每格数量（volume_per_grid）
        # 如果规则中有 volume_per_grid，使用它；否则使用 volume 作为后备
        if rule_type in ['grid_buy', 'grid_sell']:
            if 'volume' in self.param_widgets:
                volume_widget = self.param_widgets['volume']
                if 'volume_per_grid' in rule:
                    volume_widget.setValue(rule['volume_per_grid'])
                elif 'volume' in rule:
                    # 如果没有 volume_per_grid，使用 volume 作为后备
                    volume_widget.setValue(rule['volume'])

        if rule_type in ('single_sell', 'breakthrough_sell') and 'activation_enabled' in self.param_widgets:
            act = rule.get('activation') if isinstance(rule.get('activation'), dict) else {}
            has_act = bool(str(act.get('activate_at') or '').strip())
            self.param_widgets['activation_enabled'].setChecked(has_act)
            if has_act:
                t = str(act.get('activate_at') or '09:50:00')
                parts = t.split(':')
                try:
                    if len(parts) >= 3:
                        qt = QTime(int(parts[0]), int(parts[1]), int(parts[2]))
                    elif len(parts) == 2:
                        qt = QTime(int(parts[0]), int(parts[1]), 0)
                    else:
                        qt = QTime(9, 50, 0)
                    self.param_widgets['activation_activate_at'].setTime(qt)
                except (TypeError, ValueError):
                    self.param_widgets['activation_activate_at'].setTime(QTime(9, 50, 0))
                mode = str(act.get('mode') or 'suppress_if_reached')
                idx = self.param_widgets['activation_mode'].findData(mode)
                if idx >= 0:
                    self.param_widgets['activation_mode'].setCurrentIndex(idx)
                check = str(act.get('check') or 'price')
                idx = self.param_widgets['activation_check'].findData(check)
                if idx >= 0:
                    self.param_widgets['activation_check'].setCurrentIndex(idx)
                try:
                    lv = float(act.get('level') or 0)
                except (TypeError, ValueError):
                    lv = 0.0
                self.param_widgets['activation_level'].setValue(lv)
    
    def get_rule(self):
        """获取规则数据"""
        rule_type = self.type_combo.currentData()
        rule_name = self.name_edit.text().strip()
        
        if not rule_name:
            try:
                rule_name = RULE_TYPE_NAMES.get(RuleType(rule_type), '未命名规则')
            except (ValueError, AttributeError):
                # 处理未定义的规则类型（如 night_buy, night_sell, scheduled_clear 等）
                type_name_map = {
                    'night_buy': '夜市买入',
                    'night_sell': '夜市卖出',
                    'scheduled_clear': '定时清仓',
                }
                rule_name = type_name_map.get(rule_type, '未命名规则')
        
        rule_data = {
            'id': self.rule.get('id') if self.is_edit_mode else f"rule_{hash(f'{rule_type}{rule_name}')}",
            'type': rule_type,
            'name': rule_name,
            'enabled': self.enabled_check.isChecked()
        }
        
        # 添加参数
        for param_name, widget in self.param_widgets.items():
            if param_name in ("activation_enabled", "activation_activate_at", "activation_mode", "activation_check", "activation_level", "enable_price_band"):
                continue
            if isinstance(widget, (QDoubleSpinBox, QSpinBox)):
                rule_data[param_name] = widget.value()

        if rule_type == "breakthrough_buy":
            from core.price_band_buy import stamp_band_breakthrough_defaults

            enable_band = False
            if "enable_price_band" in self.param_widgets:
                enable_band = bool(self.param_widgets["enable_price_band"].isChecked())
            if enable_band:
                # price 与上沿对齐
                if "band_high" in rule_data:
                    rule_data["price"] = float(rule_data["band_high"])
                stamp_band_breakthrough_defaults(rule_data)
            else:
                for k in ("band_low", "band_high", "band_accept_low", "true_breakthrough_window_sec"):
                    rule_data.pop(k, None)
        
        # 特殊处理：网格规则
        # 保存 num_grids 时，同时设置 max_grids 为相同的值（保持兼容性）
        if rule_type in ['grid_buy', 'grid_sell']:
            if 'num_grids' in rule_data:
                rule_data['max_grids'] = rule_data['num_grids']  # 保持兼容性
        
        # 特殊处理：网格规则的每格数量
        # 保存 volume 时，同时保存为 volume_per_grid（保持兼容性）
        if rule_type in ['grid_buy', 'grid_sell']:
            if 'volume' in rule_data:
                rule_data['volume_per_grid'] = rule_data['volume']  # 保持兼容性

        if rule_type in ('single_sell', 'breakthrough_sell') and 'activation_enabled' in self.param_widgets:
            if self.param_widgets['activation_enabled'].isChecked():
                from core.rule_activation import build_activation_dict

                qt = self.param_widgets['activation_activate_at'].time()
                activate_at = f"{qt.hour():02d}:{qt.minute():02d}:{qt.second():02d}"
                mode = self.param_widgets['activation_mode'].currentData()
                check = self.param_widgets['activation_check'].currentData()
                lv = float(self.param_widgets['activation_level'].value())
                level = lv if lv > 0 else None
                act = build_activation_dict(
                    activate_at=activate_at,
                    mode=mode,
                    level=level,
                    check=check,
                    activated=False,
                )
                if self.is_edit_mode and isinstance((self.rule or {}).get('activation'), dict):
                    old = self.rule['activation']
                    try:
                        same_cfg = (
                            str(old.get('activate_at') or '') == str(act.get('activate_at') or '')
                            and str(old.get('mode') or '') == str(act.get('mode') or '')
                            and str(old.get('check') or '') == str(act.get('check') or '')
                            and round(float(old.get('level') or 0), 4) == round(float(act.get('level') or 0), 4)
                        )
                    except (TypeError, ValueError):
                        same_cfg = False
                    if same_cfg and old.get('resolved'):
                        for k in ('resolved', 'reached', 'activated', 'seal_streak'):
                            if k in old:
                                act[k] = old[k]
                rule_data['activation'] = act
            else:
                rule_data.pop('activation', None)

        # 单点/网格：新建快照当前提前下单；编辑保留原快照（缺字段则用当前默认迁移）
        if rule_type in ('single_buy', 'single_sell', 'grid_buy', 'grid_sell'):
            if self.is_edit_mode and isinstance(self.rule, dict) and 'early_order_enabled' in self.rule:
                rule_data['early_order_enabled'] = bool(self.rule.get('early_order_enabled'))
            else:
                rule_data['early_order_enabled'] = bool(self._load_early_order())

        if rule_type == 'breakthrough_buy':
            from core.trading_config import (
                breakthrough_buy_require_true_breakthrough,
                breakthrough_buy_require_break_below_trigger,
            )
            from core.price_band_buy import rule_has_price_band, stamp_band_breakthrough_defaults

            if (
                self.is_edit_mode
                and isinstance(self.rule, dict)
                and 'require_true_breakthrough' in self.rule
            ):
                rule_data['require_true_breakthrough'] = bool(
                    self.rule.get('require_true_breakthrough')
                )
            else:
                rule_data['require_true_breakthrough'] = bool(
                    breakthrough_buy_require_true_breakthrough(default=False)
                )
            if (
                self.is_edit_mode
                and isinstance(self.rule, dict)
                and 'require_break_below' in self.rule
            ):
                rule_data['require_break_below'] = bool(self.rule.get('require_break_below'))
            else:
                rule_data['require_break_below'] = bool(
                    breakthrough_buy_require_break_below_trigger(default=False)
                )
            if rule_has_price_band(rule_data):
                stamp_band_breakthrough_defaults(rule_data)
        
        return rule_data

