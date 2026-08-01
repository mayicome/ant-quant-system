#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
任务设置对话框
用于配置全局任务设置（人工审核、提前下单等）
"""

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QPushButton,
                             QCheckBox, QGroupBox, QMessageBox, QLabel, QTimeEdit, QDoubleSpinBox)
from PyQt5.QtCore import Qt, QTime
import configparser
import os


class TaskSettingsDialog(QDialog):
    """任务设置对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("任务设置")
        self.setMinimumWidth(400)
        self.setMinimumHeight(280)
        
        self.setup_ui()
        self.load_settings()
    
    def setup_ui(self):
        """设置UI"""
        layout = QVBoxLayout(self)
        
        # 标题
        title_label = QLabel("全局任务设置")
        title_label.setStyleSheet("font-size: 14px; font-weight: bold; padding: 10px;")
        layout.addWidget(title_label)
        
        # 任务设置区域
        settings_group = QGroupBox("交易设置")
        settings_layout = QVBoxLayout()
        
        # 人工审核开关
        self.require_manual_approval_checkbox = QCheckBox("需要人工审核")
        self.require_manual_approval_checkbox.setToolTip("启用后，每笔订单发出前会弹窗让用户确认")
        settings_layout.addWidget(self.require_manual_approval_checkbox)
        
        # 提前下单开关（仅单点/网格；突破买入提前挂出会直接排队成交，与突破逻辑冲突）
        self.early_order_checkbox = QCheckBox("提前下单（只针对单点和网格）")
        self.early_order_checkbox.setToolTip(
            "仅作为新创建单点/网格规则的默认：创建时快照到该规则。\n"
            "之后改此开关不影响已有规则；已有规则保持创建时的提前/不提前状态。\n"
            "价格接近触发价时提前挂单，到达后自动确认。\n"
            "突破买入/卖出、笼子、弹性订单不适用提前下单。"
        )
        settings_layout.addWidget(self.early_order_checkbox)

        self.non_early_sell_smart_sell_cb = QCheckBox("非提前下单卖出任务智能卖出")
        self.non_early_sell_smart_sell_cb.setToolTip(
            "启用后：除「提前下单」外的卖出任务（含突破卖出、单点卖出、定时清仓等）"
            "在触发后将按实时盘口灵活处理卖出。\n"
            "未启用时：与当前卖出逻辑相同。\n"
            "弹性卖出不受此项影响；已通过提前下单挂出的卖单也不走智能卖出。"
        )
        settings_layout.addWidget(self.non_early_sell_smart_sell_cb)

        # 全局最小买入金额（拦小买单 / 等同临时禁买）
        min_buy_layout = QHBoxLayout()
        min_buy_layout.addWidget(QLabel("最小买入金额:"))
        self.min_buy_amount_spin = QDoubleSpinBox()
        self.min_buy_amount_spin.setRange(0, 99999999)
        self.min_buy_amount_spin.setDecimals(0)
        self.min_buy_amount_spin.setSingleStep(500)
        self.min_buy_amount_spin.setSuffix(" 元")
        self.min_buy_amount_spin.setToolTip(
            "只卡本笔买入金额（价×量），与可用现金无关。\n"
            "低于该值：不开单并结束任务，避免小单浪费手续费。\n"
            "设很大的值可临时禁止买入；设 0 表示不限制。"
        )
        min_buy_layout.addWidget(self.min_buy_amount_spin)
        min_buy_layout.addStretch()
        settings_layout.addLayout(min_buy_layout)

        self.breakthrough_true_break_cb = QCheckBox("突破买入是否判断真突破")
        self.breakthrough_true_break_cb.setToolTip(
            "仅作为新创建突破买入规则的默认：创建时快照到该规则。\n"
            "之后改此开关不影响已有规则；已有规则保持创建时的判断/不判断状态。\n"
            "启用后：在价格首次上穿触发价的那一笔 tick 判断真突破（与 breakbuycheck / intelligentbuy 一致）。\n"
            "· 未开试探建仓：满足真突破则一次性全量买入，否则结束规则（非真突破未下单）。\n"
            "· 已开试探建仓：须先过真突破才买试探 20%，剩余 80% 由确认窗口决定；不过真突破则整笔放弃。\n"
            "  若 20% 不足 1 手（如 300 股）：与不开试探相同，真突破通过后一次性全量买入。\n"
            "未启用：上穿触发价即买入（不判真突破）；builtin 下同样按规则快照执行。"
        )
        settings_layout.addWidget(self.breakthrough_true_break_cb)

        self.breakthrough_break_below_cb = QCheckBox("突破买入：高于触发价时须先跌破")
        self.breakthrough_break_below_cb.setToolTip(
            "仅作为新创建突破买入规则的默认：创建时快照到该规则；改开关不影响已有规则。\n"
            "启用后：若任务生效时或盘中现价高于触发价，不会立即按「已在上方便上穿」成交；"
            "须先出现一次「从 ≥ 触发价 跌至 < 触发价」，再等待首次上穿触发价"
            "（若该规则已开「判断真突破」则同时满足真突破）。\n"
            "若现价低于触发价：仍按普通突破，等价格上穿触发价即可（不要求先跌破）。\n"
            "未启用时：现价高于触发价时，首 tick 也可能视为上穿。"
        )
        settings_layout.addWidget(self.breakthrough_break_below_cb)

        self.breakthrough_probe_cb = QCheckBox("突破买入试探建仓（先 20% 后确认补 80%）")
        self.breakthrough_probe_cb.setToolTip(
            "启用后：突破上穿时先买入规则 volume 的 20%（策略「一半金额」之上再拆，"
            "剩余 80% 由 45 秒确认窗口决定是否补买）。\n"
            "· 未开「判断真突破」：上穿即买 20%，后续靠确认窗口。\n"
            "· 已开「判断真突破」：须上穿 tick 过真突破才买 20%，不过则整笔放弃；80% 仍靠确认窗口。\n"
            "确认窗口：跌回触发价放弃；延续强势则补买 80%；"
            "追价上限 +2.5%，真突破量比≥3 时不因追价放弃。\n"
            "强突破（量比≥3）首轮确认未补买时：可跌回再上穿重试补买（仅补买、不再下试探仓），"
            "每日最多 2 次失败的补买确认窗，截止 14:57。\n"
            "20% 不足 1 手时退回一次性全买；仍不足 1 手或低于最小买入金额时亦退回全买。"
        )
        settings_layout.addWidget(self.breakthrough_probe_cb)

        # 禁买时间窗口
        self.buy_block_window_checkbox = QCheckBox(
            "启动禁买时间窗口（该时段普通买入触发则跳过并结束；提前下单不受限）"
        )
        self.buy_block_window_checkbox.setToolTip(
            "启用后，在设定时间窗内普通触发的买入任务将标记为已执行，但不实际下单。\n"
            "提前下单不受此项限制：窗内仍可能提前挂出或成交。"
        )
        settings_layout.addWidget(self.buy_block_window_checkbox)

        time_layout = QHBoxLayout()
        self.buy_block_start_label = QLabel("禁买开始:")
        time_layout.addWidget(self.buy_block_start_label)
        self.buy_block_start_time = QTimeEdit()
        self.buy_block_start_time.setDisplayFormat("HH:mm:ss")
        self.buy_block_start_time.setTime(QTime(9, 30, 0))
        time_layout.addWidget(self.buy_block_start_time)
        self.buy_block_end_label = QLabel("禁买结束:")
        time_layout.addWidget(self.buy_block_end_label)
        self.buy_block_end_time = QTimeEdit()
        self.buy_block_end_time.setDisplayFormat("HH:mm:ss")
        self.buy_block_end_time.setTime(QTime(9, 31, 30))
        time_layout.addWidget(self.buy_block_end_time)
        settings_layout.addLayout(time_layout)

        self.buy_block_window_checkbox.toggled.connect(self._sync_buy_block_controls_enabled)
        
        settings_group.setLayout(settings_layout)
        layout.addWidget(settings_group)
        
        # 底部按钮
        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()
        
        save_btn = QPushButton("保存")
        save_btn.clicked.connect(self.accept)
        bottom_layout.addWidget(save_btn)
        
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        bottom_layout.addWidget(cancel_btn)
        
        layout.addLayout(bottom_layout)
    
    def load_settings(self):
        """加载设置"""
        require_approval = self._load_require_manual_approval()
        self.require_manual_approval_checkbox.setChecked(require_approval)
        self._sync_manual_approval_for_qmt_mode()
        
        early_order = self._load_early_order()
        self.early_order_checkbox.setChecked(early_order)
        self.non_early_sell_smart_sell_cb.setChecked(
            self._load_non_early_order_sell_smart_sell()
        )
        self._sync_non_early_smart_sell_for_qmt_mode()
        self.breakthrough_true_break_cb.setChecked(
            self._load_breakthrough_require_true_breakthrough()
        )
        self.breakthrough_break_below_cb.setChecked(
            self._load_breakthrough_require_break_below_trigger()
        )
        self.breakthrough_probe_cb.setChecked(
            self._load_breakthrough_probe_enabled()
        )
        self._sync_breakthrough_probe_for_qmt_mode()
        self.min_buy_amount_spin.setValue(self._load_min_buy_amount())
        enabled, start_s, end_s = self._load_buy_block_window_config()
        self.buy_block_window_checkbox.setChecked(enabled)
        self.buy_block_start_time.setTime(self._to_qtime(start_s, QTime(9, 30, 0)))
        self.buy_block_end_time.setTime(self._to_qtime(end_s, QTime(9, 31, 30)))
        self._sync_buy_block_controls_enabled()

    def _is_builtin_order_mode(self) -> bool:
        try:
            from utils.qmt_execution_config import use_builtin_order_execution

            return bool(use_builtin_order_execution())
        except Exception:
            return False

    def _sync_manual_approval_for_qmt_mode(self):
        """builtin/standalone：下单在大 QMT，无法弹主程序审核窗，灰掉该选项。"""
        builtin = self._is_builtin_order_mode()
        self.require_manual_approval_checkbox.setEnabled(not builtin)
        if builtin:
            self.require_manual_approval_checkbox.setToolTip(
                "当前 qmt_mode 为 builtin/standalone：下单由大 QMT 内置策略自动执行，"
                "不支持主程序人工审核弹窗。\n"
                "切换为 mini 后可再启用。"
            )
        else:
            self.require_manual_approval_checkbox.setToolTip(
                "启用后，每笔订单发出前会弹窗让用户确认"
            )

    def _sync_non_early_smart_sell_for_qmt_mode(self):
        """builtin/standalone：卖出由大 QMT 限价直达，无主程序智能卖出会话。"""
        builtin = self._is_builtin_order_mode()
        self.non_early_sell_smart_sell_cb.setEnabled(not builtin)
        if builtin:
            self.non_early_sell_smart_sell_cb.setToolTip(
                "当前 qmt_mode 为 builtin/standalone：卖出由大 QMT 按触发价限价下单，"
                "不支持主程序「智能卖出」盘口改价/强平流程。\n"
                "切换为 mini 后可再启用。"
            )
        else:
            self.non_early_sell_smart_sell_cb.setToolTip(
                "启用后：除「提前下单」外的卖出任务（含突破卖出、单点卖出、定时清仓等）"
                "在触发后将按实时盘口灵活处理卖出。\n"
                "未启用时：与当前卖出逻辑相同。\n"
                "弹性卖出不受此项影响；已通过提前下单挂出的卖单也不走智能卖出。"
            )

    def _sync_breakthrough_probe_for_qmt_mode(self):
        """builtin/standalone：突破由大 QMT 一次下单，无主程序试探确认窗。"""
        builtin = self._is_builtin_order_mode()
        self.breakthrough_probe_cb.setEnabled(not builtin)
        if builtin:
            self.breakthrough_probe_cb.setToolTip(
                "当前 qmt_mode 为 builtin/standalone：突破买入由大 QMT 按触发条件一次性下单，"
                "不支持主程序「试探 20% + 确认补 80%」流程。\n"
                "切换为 mini 后可再启用。"
            )
        else:
            self.breakthrough_probe_cb.setToolTip(
                "启用后：突破上穿时先买入规则 volume 的 20%（策略「一半金额」之上再拆，"
                "剩余 80% 由 45 秒确认窗口决定是否补买）。\n"
                "· 未开「判断真突破」：上穿即买 20%，后续靠确认窗口。\n"
                "· 已开「判断真突破」：须上穿 tick 过真突破才买 20%，不过则整笔放弃；80% 仍靠确认窗口。\n"
                "确认窗口：跌回触发价放弃；延续强势则补买 80%；"
                "追价上限 +2.5%，真突破量比≥3 时不因追价放弃。\n"
                "强突破（量比≥3）首轮确认未补买时：可跌回再上穿重试补买（仅补买、不再下试探仓），"
                "每日最多 2 次失败的补买确认窗，截止 14:57。\n"
                "20% 不足 1 手时退回一次性全买；仍不足 1 手或低于最小买入金额时亦退回全买。"
            )

    def _sync_buy_block_controls_enabled(self):
        """未启用开盘禁买时间窗时，禁买起止时间行置灰不可编辑，避免误以为已生效。"""
        on = self.buy_block_window_checkbox.isChecked()
        self.buy_block_start_label.setEnabled(on)
        self.buy_block_start_time.setEnabled(on)
        self.buy_block_end_label.setEnabled(on)
        self.buy_block_end_time.setEnabled(on)
    
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
    
    def _load_non_early_order_sell_smart_sell(self):
        try:
            from core.trading_config import non_early_order_sell_smart_sell_enabled

            return non_early_order_sell_smart_sell_enabled(default=False)
        except Exception:
            return False

    def _save_non_early_order_sell_smart_sell(self, value):
        try:
            from core.trading_config import write_trading_bool

            if not write_trading_bool("non_early_order_sell_smart_sell", bool(value)):
                raise RuntimeError("write failed")
            return True
        except Exception as e:
            QMessageBox.warning(self, "警告", f"保存配置失败: {str(e)}")
            return False

    def _load_breakthrough_require_true_breakthrough(self):
        try:
            from core.trading_config import breakthrough_buy_require_true_breakthrough

            return breakthrough_buy_require_true_breakthrough(default=False)
        except Exception:
            return False

    def _save_breakthrough_require_true_breakthrough(self, value):
        try:
            from core.trading_config import write_trading_bool

            if not write_trading_bool("breakthrough_buy_require_true_breakthrough", bool(value)):
                raise RuntimeError("write failed")
            return True
        except Exception as e:
            QMessageBox.warning(self, "警告", f"保存配置失败: {str(e)}")
            return False

    def _load_breakthrough_probe_enabled(self):
        try:
            from core.trading_config import breakthrough_buy_probe_enabled

            return breakthrough_buy_probe_enabled(default=False)
        except Exception:
            return False

    def _save_breakthrough_probe_enabled(self, value):
        try:
            from core.trading_config import write_trading_bool

            if not write_trading_bool("breakthrough_buy_probe_enabled", bool(value)):
                raise RuntimeError("write failed")
            return True
        except Exception as e:
            QMessageBox.warning(self, "警告", f"保存配置失败: {str(e)}")
            return False

    def _load_breakthrough_require_break_below_trigger(self):
        try:
            from core.trading_config import breakthrough_buy_require_break_below_trigger

            return breakthrough_buy_require_break_below_trigger(default=False)
        except Exception:
            return False

    def _save_breakthrough_require_break_below_trigger(self, value):
        try:
            from core.trading_config import write_trading_bool

            if not write_trading_bool(
                "breakthrough_buy_require_break_below_trigger", bool(value)
            ):
                raise RuntimeError("write failed")
            return True
        except Exception as e:
            QMessageBox.warning(self, "警告", f"保存配置失败: {str(e)}")
            return False

    def _load_min_buy_amount(self):
        """从config.ini加载全局最小买入金额（元），默认5000。"""
        try:
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(current_dir, 'data', 'config.ini')
            if os.path.exists(config_path):
                config = configparser.ConfigParser()
                config.read(config_path, encoding='utf-8')
                if 'Trading' in config:
                    value = float(config.get('Trading', 'min_buy_amount', fallback='5000'))
                    return max(0.0, value)
            return 5000.0
        except Exception:
            return 5000.0

    def _save_min_buy_amount(self, value):
        """保存全局最小买入金额到config.ini。"""
        try:
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(current_dir, 'data', 'config.ini')
            os.makedirs(os.path.dirname(config_path), exist_ok=True)

            config = configparser.ConfigParser()
            if os.path.exists(config_path):
                config.read(config_path, encoding='utf-8')
            if 'Trading' not in config:
                config.add_section('Trading')
            config.set('Trading', 'min_buy_amount', str(int(max(0.0, float(value)))))

            with open(config_path, 'w', encoding='utf-8') as f:
                config.write(f)
            return True
        except Exception as e:
            QMessageBox.warning(self, "警告", f"保存配置失败: {str(e)}")
            return False

    def _to_qtime(self, s, default_time):
        try:
            if not s:
                return default_time
            parts = [int(x) for x in str(s).strip().split(":")]
            if len(parts) == 2:
                h, m = parts
                sec = 0
            elif len(parts) >= 3:
                h, m, sec = parts[:3]
            else:
                return default_time
            return QTime(h, m, sec)
        except Exception:
            return default_time

    def _load_buy_block_window_config(self):
        """读取开盘禁买时间窗配置。"""
        try:
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(current_dir, 'data', 'config.ini')
            if not os.path.exists(config_path):
                return False, "09:30:00", "09:31:30"
            config = configparser.ConfigParser()
            config.read(config_path, encoding='utf-8')
            if 'Trading' not in config:
                return False, "09:30:00", "09:31:30"
            enabled = config.get('Trading', 'buy_block_window_enabled', fallback='0').lower() in ('1', 'true', 'yes', 'on')
            start_s = config.get('Trading', 'buy_block_start', fallback='09:30:00').strip()
            end_s = config.get('Trading', 'buy_block_end', fallback='09:31:30').strip()
            return enabled, start_s, end_s
        except Exception:
            return False, "09:30:00", "09:31:30"

    def _save_buy_block_window_config(self, enabled, start_s, end_s):
        """保存开盘禁买时间窗配置。"""
        try:
            current_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            config_path = os.path.join(current_dir, 'data', 'config.ini')
            os.makedirs(os.path.dirname(config_path), exist_ok=True)
            config = configparser.ConfigParser()
            if os.path.exists(config_path):
                config.read(config_path, encoding='utf-8')
            if 'Trading' not in config:
                config.add_section('Trading')
            config.set('Trading', 'buy_block_window_enabled', '1' if enabled else '0')
            config.set('Trading', 'buy_block_start', start_s)
            config.set('Trading', 'buy_block_end', end_s)
            with open(config_path, 'w', encoding='utf-8') as f:
                config.write(f)
            return True
        except Exception as e:
            QMessageBox.warning(self, "警告", f"保存配置失败: {str(e)}")
            return False
    
    def accept(self):
        """保存设置并关闭对话框"""
        # builtin 下「人工审核 / 智能卖出 / 试探建仓」灰掉，不改写对应配置
        skip_chart_only = self._is_builtin_order_mode()
        if not skip_chart_only:
            self._save_require_manual_approval(
                self.require_manual_approval_checkbox.isChecked()
            )
            self._save_non_early_order_sell_smart_sell(
                self.non_early_sell_smart_sell_cb.isChecked()
            )
            self._save_breakthrough_probe_enabled(
                self.breakthrough_probe_cb.isChecked()
            )

        self._save_early_order(self.early_order_checkbox.isChecked())
        self._save_breakthrough_require_true_breakthrough(
            self.breakthrough_true_break_cb.isChecked()
        )
        self._save_breakthrough_require_break_below_trigger(
            self.breakthrough_break_below_cb.isChecked()
        )
        self._save_min_buy_amount(self.min_buy_amount_spin.value())
        self._save_buy_block_window_config(
            self.buy_block_window_checkbox.isChecked(),
            self.buy_block_start_time.time().toString("HH:mm:ss"),
            self.buy_block_end_time.time().toString("HH:mm:ss"),
        )

        QMessageBox.information(self, "提示", "设置已保存")
        super().accept()

