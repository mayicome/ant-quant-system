#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
交易规则定义模块
定义各种交易规则类型及其参数结构
"""

import uuid
from enum import Enum
from typing import Any, Dict, List

class RuleType(Enum):
    """规则类型枚举"""
    SINGLE_BUY = 'single_buy'           # 单点买入
    SINGLE_SELL = 'single_sell'         # 单点卖出
    CAGE_BUY = 'cage_buy'               # 笼子买入
    CAGE_SELL = 'cage_sell'             # 笼子卖出
    BEST_SELL = 'best_sell'             # 弹性卖出
    BEST_BUY = 'best_buy'               # 弹性买入
    GRID_BUY = 'grid_buy'               # 网格买入
    GRID_SELL = 'grid_sell'             # 网格卖出
    BREAKTHROUGH_BUY = 'breakthrough_buy'   # 突破买入
    BREAKTHROUGH_SELL = 'breakthrough_sell' # 突破卖出
    NIGHT_BUY = 'night_buy'             # 夜市买入
    NIGHT_SELL = 'night_sell'           # 夜市卖出
    SCHEDULED_CLEAR = 'scheduled_clear'  # 定时清仓

# 规则类型的中文名称
RULE_TYPE_NAMES = {
    RuleType.SINGLE_BUY: '单点买入',
    RuleType.SINGLE_SELL: '单点卖出',
    RuleType.CAGE_BUY: '笼子买入',
    RuleType.CAGE_SELL: '笼子卖出',
    RuleType.BEST_SELL: '弹性卖出',
    RuleType.BEST_BUY: '弹性买入',
    RuleType.GRID_BUY: '网格买入',
    RuleType.GRID_SELL: '网格卖出',
    RuleType.BREAKTHROUGH_BUY: '突破买入',
    RuleType.BREAKTHROUGH_SELL: '突破卖出',
    RuleType.NIGHT_BUY: '夜市买入',
    RuleType.NIGHT_SELL: '夜市卖出',
    RuleType.SCHEDULED_CLEAR: '定时清仓',
}

# 规则类型的颜色配置（与图表「规则工具」按钮底色一致）
RULE_TYPE_COLORS = {
    RuleType.SINGLE_BUY: '#4caf50',      # 绿色
    RuleType.SINGLE_SELL: '#f44336',     # 红色
    RuleType.CAGE_BUY: '#66bb6a',        # 浅绿色
    RuleType.CAGE_SELL: '#ef5350',       # 浅红色
    RuleType.BEST_SELL: '#ec407a',       # 粉色（弹性卖出）
    RuleType.BEST_BUY: '#26a69a',        # 青绿（弹性买入）
    RuleType.GRID_BUY: '#00897b',        # 深青绿（网格买入）
    RuleType.GRID_SELL: '#d32f2f',       # 深红（网格卖出）
    RuleType.BREAKTHROUGH_BUY: '#00bcd4',  # 青色
    RuleType.BREAKTHROUGH_SELL: '#7b1fa2', # 深紫色（与单点卖出的红色和夜市卖出的紫色有明显区别）
    RuleType.NIGHT_BUY: '#5c6bc0',       # 深蓝色（与按钮颜色一致）
    RuleType.NIGHT_SELL: '#ab47bc',      # 紫色（与按钮颜色一致）
    RuleType.SCHEDULED_CLEAR: '#9c27b0',  # 紫色（与图表显示颜色一致）
}

# 节点：填充=类型色；描边/透明度/标记=运行状态（避免类型与状态抢同一色相）
RULE_NODE_STATE_STYLES = {
    "pending": {"edge": "#212121", "lw": 1.0, "alpha": 1.0, "marker": "o"},
    "early_order": {"edge": "#FDD835", "lw": 2.8, "alpha": 1.0, "marker": "o"},   # 黄边=提前挂单
    "triggered": {"edge": "#00E5FF", "lw": 2.8, "alpha": 1.0, "marker": "o"},     # 亮边=已触发盯盘
    "cash_wait": {"edge": "#E65100", "lw": 2.8, "alpha": 1.0, "marker": "o"},  # 深橙边=等资金/再挂
    "done": {"edge": "#616161", "lw": 1.0, "alpha": 0.35, "marker": "o"},        # 淡=正常结束
    # 禁用：仍为圆点，绘制时再叠一层黑叉（overlay_x）
    "disabled": {
        "edge": "#000000", "lw": 2.0, "alpha": 0.7, "marker": "o", "overlay_x": True,
    },
    "abnormal": {"edge": "#B71C1C", "lw": 2.4, "alpha": 0.40, "marker": "o"},   # 暗红边+淡填充=异常结束
    "halt": {"edge": "#FFC107", "lw": 2.8, "alpha": 0.75, "marker": "o"},       # 琥珀边=熔断停止
    "ghost": {"edge": "#9E9E9E", "lw": 1.0, "alpha": 0.22, "marker": "o"},       # 笼未触发端
}


def get_rule_type_color(rule_type: Any, default: str = "#888888") -> str:
    """规则类型填充色（与工具栏一致）。"""
    if rule_type is None:
        return default
    try:
        if isinstance(rule_type, RuleType):
            return RULE_TYPE_COLORS.get(rule_type, default)
        return RULE_TYPE_COLORS.get(RuleType(str(rule_type).strip()), default)
    except (ValueError, AttributeError, TypeError):
        return default


def get_rule_node_state_style(state: str) -> Dict[str, Any]:
    """返回节点绘制样式副本。"""
    base = RULE_NODE_STATE_STYLES.get(state) or RULE_NODE_STATE_STYLES["pending"]
    out = {
        "edge": base["edge"],
        "lw": float(base["lw"]),
        "alpha": float(base["alpha"]),
        "marker": base["marker"],
    }
    if base.get("overlay_x"):
        out["overlay_x"] = True
    if base.get("fill"):
        out["fill"] = base["fill"]
    return out


def _rule_executed_is_abnormal(rule: Dict[str, Any]) -> bool:
    """已执行是否属于异常结束（叉），其余为正常淡化结束。"""
    executed_reason = str(rule.get("executed_reason", "") or "")
    executed_order_id = str(rule.get("order_id", "") or "")
    if executed_reason in (
        "buy_block_window",
        "order_below_min",
        "early_cancelled",
        "not_true_breakthrough",
        "band_hard_pass",
        "order_failed",
        "no_cash",
        "no_position",
        "pending_sell_lock",
    ):
        return True
    if executed_order_id in (
        "SKIPPED_BUY_WINDOW",
        "SKIPPED_MIN_BUY",
        "EARLY_CANCELLED",
        "BAND_HARD_PASS",
        "ORDER_FAILED",
        "MIN_BUY_AMOUNT",
        "NO_CASH",
    ):
        return True
    return False


def classify_rule_chart_state(rule: Dict[str, Any], rule_type: Any = None) -> str:
    """根据规则字段得到基础状态（不含提前挂单/弹性已触发等运行时叠加）。"""
    rt = rule_type or rule.get("type") or rule.get("rule_type")
    rt = str(rt).strip() if rt is not None else ""
    enabled = bool(rule.get("enabled", True))

    if rt == "scheduled_clear":
        if rule.get("scheduled_clear_executed"):
            return "done"
        if not enabled:
            if str(rule.get("halt_reason") or "").strip() == "open_gain":
                return "halt"
            return "disabled"
        return "pending"

    if rule.get("executed", False):
        return "abnormal" if _rule_executed_is_abnormal(rule) else "done"

    if not enabled:
        if str(rule.get("halt_reason") or "").strip() == "open_gain":
            return "halt"
        return "disabled"

    if bool(rule.get("cash_wait_active")):
        return "cash_wait"

    return "pending"

class TradingRule:
    """交易规则基类"""
    
    def __init__(self, rule_type: RuleType, rule_id: str = None, enabled: bool = True, name: str = None):
        self.id = rule_id or f"rule_{uuid.uuid4().hex[:8]}"
        self.type = rule_type.value if isinstance(rule_type, RuleType) else rule_type
        self.enabled = enabled
        self.name = name or RULE_TYPE_NAMES.get(rule_type, '未命名规则')
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'id': self.id,
            'type': self.type,
            'enabled': self.enabled,
            'name': self.name
        }
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'TradingRule':
        """从字典创建规则对象"""
        rule_type = data.get('type')
        
        if rule_type == RuleType.SINGLE_BUY.value:
            return SingleBuyRule.from_dict(data)
        elif rule_type == RuleType.SINGLE_SELL.value:
            return SingleSellRule.from_dict(data)
        elif rule_type == RuleType.CAGE_BUY.value:
            return CageBuyRule.from_dict(data)
        elif rule_type == RuleType.CAGE_SELL.value:
            return CageSellRule.from_dict(data)
        elif rule_type == RuleType.BEST_SELL.value:
            return BestSellRule.from_dict(data)
        elif rule_type == RuleType.BEST_BUY.value:
            return BestBuyRule.from_dict(data)
        elif rule_type == RuleType.GRID_BUY.value:
            return GridBuyRule.from_dict(data)
        elif rule_type == RuleType.GRID_SELL.value:
            return GridSellRule.from_dict(data)
        elif rule_type == RuleType.BREAKTHROUGH_BUY.value:
            return BreakthroughBuyRule.from_dict(data)
        elif rule_type == RuleType.BREAKTHROUGH_SELL.value:
            return BreakthroughSellRule.from_dict(data)
        else:
            return TradingRule(rule_type, data.get('id'), data.get('enabled', True), data.get('name'))

class SingleBuyRule(TradingRule):
    """单点买入规则"""
    
    def __init__(self, price: float, volume: int, rule_id: str = None, enabled: bool = True, name: str = None):
        super().__init__(RuleType.SINGLE_BUY, rule_id, enabled, name)
        self.price = price
        self.volume = volume
    
    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data.update({
            'price': self.price,
            'volume': self.volume
        })
        return data
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'SingleBuyRule':
        return SingleBuyRule(
            price=data.get('price', 0),
            volume=data.get('volume', 0),
            rule_id=data.get('id'),
            enabled=data.get('enabled', True),
            name=data.get('name')
        )

class SingleSellRule(TradingRule):
    """单点卖出规则"""
    
    def __init__(self, price: float, volume: int, rule_id: str = None, enabled: bool = True, name: str = None):
        super().__init__(RuleType.SINGLE_SELL, rule_id, enabled, name)
        self.price = price
        self.volume = volume
    
    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data.update({
            'price': self.price,
            'volume': self.volume
        })
        return data
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'SingleSellRule':
        return SingleSellRule(
            price=data.get('price', 0),
            volume=data.get('volume', 0),
            rule_id=data.get('id'),
            enabled=data.get('enabled', True),
            name=data.get('name')
        )

class CageBuyRule(TradingRule):
    """笼子买入规则"""
    
    def __init__(self, price_low: float, price_high: float, volume: int, 
                 rule_id: str = None, enabled: bool = True, name: str = None):
        super().__init__(RuleType.CAGE_BUY, rule_id, enabled, name)
        self.price_low = price_low
        self.price_high = price_high
        self.volume = volume
    
    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data.update({
            'price_low': self.price_low,
            'price_high': self.price_high,
            'volume': self.volume
        })
        return data
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'CageBuyRule':
        return CageBuyRule(
            price_low=data.get('price_low', 0),
            price_high=data.get('price_high', 0),
            volume=data.get('volume', 0),
            rule_id=data.get('id'),
            enabled=data.get('enabled', True),
            name=data.get('name')
        )

class CageSellRule(TradingRule):
    """笼子卖出规则"""
    
    def __init__(self, price_low: float, price_high: float, volume: int,
                 rule_id: str = None, enabled: bool = True, name: str = None):
        super().__init__(RuleType.CAGE_SELL, rule_id, enabled, name)
        self.price_low = price_low
        self.price_high = price_high
        self.volume = volume
    
    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data.update({
            'price_low': self.price_low,
            'price_high': self.price_high,
            'volume': self.volume
        })
        return data
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'CageSellRule':
        return CageSellRule(
            price_low=data.get('price_low', 0),
            price_high=data.get('price_high', 0),
            volume=data.get('volume', 0),
            rule_id=data.get('id'),
            enabled=data.get('enabled', True),
            name=data.get('name')
        )

class BestSellRule(TradingRule):
    """弹性卖出规则"""
    
    def __init__(self, trigger_price: float, drop_percent: float, volume: int = 0,
                 rule_id: str = None, enabled: bool = True, name: str = None,
                 pullback_price: float = None, room_blend_start: float = None):
        super().__init__(RuleType.BEST_SELL, rule_id, enabled, name)
        self.trigger_price = trigger_price      # 触发价格
        self.drop_percent = drop_percent        # 从最高价回落百分比（未设 pullback_price 时生效）
        self.pullback_price = pullback_price    # 可选：回落至该绝对价卖出
        self.room_blend_start = room_blend_start  # 距涨停 pp 起开始往近板收紧
        self.volume = volume                    # 卖出数量，0表示全部
    
    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data.update({
            'trigger_price': self.trigger_price,
            'drop_percent': self.drop_percent,
            'volume': self.volume
        })
        if self.pullback_price is not None and self.pullback_price > 0:
            data['pullback_price'] = self.pullback_price
        if self.room_blend_start is not None and self.room_blend_start > 0:
            data['room_blend_start'] = self.room_blend_start
        return data
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'BestSellRule':
        raw_pp = data.get('pullback_price')
        pp = None
        if raw_pp is not None:
            try:
                fv = float(raw_pp)
                if fv > 0:
                    pp = fv
            except (TypeError, ValueError):
                pass
        raw_blend = data.get('room_blend_start')
        blend = None
        if raw_blend is not None:
            try:
                fv = float(raw_blend)
                if fv > 0:
                    blend = fv
            except (TypeError, ValueError):
                pass
        return BestSellRule(
            trigger_price=data.get('trigger_price', 0),
            drop_percent=data.get('drop_percent', 0.3),
            volume=data.get('volume', 0),
            rule_id=data.get('id'),
            enabled=data.get('enabled', True),
            name=data.get('name'),
            pullback_price=pp,
            room_blend_start=blend,
        )

class BestBuyRule(TradingRule):
    """弹性买入规则"""
    
    def __init__(self, trigger_price: float, rise_percent: float, volume: int,
                 rule_id: str = None, enabled: bool = True, name: str = None):
        super().__init__(RuleType.BEST_BUY, rule_id, enabled, name)
        self.trigger_price = trigger_price      # 触发价格
        self.rise_percent = rise_percent        # 从最低价反弹百分比
        self.volume = volume
    
    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data.update({
            'trigger_price': self.trigger_price,
            'rise_percent': self.rise_percent,
            'volume': self.volume
        })
        return data
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'BestBuyRule':
        return BestBuyRule(
            trigger_price=data.get('trigger_price', 0),
            rise_percent=data.get('rise_percent', 0.3),
            volume=data.get('volume', 0),
            rule_id=data.get('id'),
            enabled=data.get('enabled', True),
            name=data.get('name')
        )

class GridBuyRule(TradingRule):
    """网格买入规则"""
    
    def __init__(self, start_price: float, grid_step: float, volume: int, max_grids: int = 10,
                 rule_id: str = None, enabled: bool = True, name: str = None):
        super().__init__(RuleType.GRID_BUY, rule_id, enabled, name)
        self.start_price = start_price      # 起始价格
        self.grid_step = grid_step          # 网格间距（价格或百分比）
        self.volume = volume                # 每次买入数量
        self.max_grids = max_grids          # 最大网格数
    
    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data.update({
            'start_price': self.start_price,
            'grid_step': self.grid_step,
            'volume': self.volume,
            'max_grids': self.max_grids
        })
        return data
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'GridBuyRule':
        return GridBuyRule(
            start_price=data.get('start_price', 0),
            grid_step=data.get('grid_step', 0.5),
            volume=data.get('volume', 0),
            max_grids=data.get('max_grids', 10),
            rule_id=data.get('id'),
            enabled=data.get('enabled', True),
            name=data.get('name')
        )

class GridSellRule(TradingRule):
    """网格卖出规则"""
    
    def __init__(self, start_price: float, grid_step: float, volume: int, max_grids: int = 10,
                 rule_id: str = None, enabled: bool = True, name: str = None):
        super().__init__(RuleType.GRID_SELL, rule_id, enabled, name)
        self.start_price = start_price
        self.grid_step = grid_step
        self.volume = volume
        self.max_grids = max_grids
    
    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data.update({
            'start_price': self.start_price,
            'grid_step': self.grid_step,
            'volume': self.volume,
            'max_grids': self.max_grids
        })
        return data
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'GridSellRule':
        return GridSellRule(
            start_price=data.get('start_price', 0),
            grid_step=data.get('grid_step', 0.5),
            volume=data.get('volume', 0),
            max_grids=data.get('max_grids', 10),
            rule_id=data.get('id'),
            enabled=data.get('enabled', True),
            name=data.get('name')
        )

class BreakthroughBuyRule(TradingRule):
    """突破买入规则：价格大于设定价时买入"""
    
    def __init__(self, price: float, volume: int, rule_id: str = None, enabled: bool = True, name: str = None):
        super().__init__(RuleType.BREAKTHROUGH_BUY, rule_id, enabled, name)
        self.price = price
        self.volume = volume
    
    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data.update({
            'price': self.price,
            'volume': self.volume
        })
        return data
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'BreakthroughBuyRule':
        return BreakthroughBuyRule(
            price=data.get('price', 0),
            volume=data.get('volume', 0),
            rule_id=data.get('id'),
            enabled=data.get('enabled', True),
            name=data.get('name')
        )

class BreakthroughSellRule(TradingRule):
    """突破卖出规则：价格小于设定价时卖出"""
    
    def __init__(self, price: float, volume: int, rule_id: str = None, enabled: bool = True, name: str = None):
        super().__init__(RuleType.BREAKTHROUGH_SELL, rule_id, enabled, name)
        self.price = price
        self.volume = volume
    
    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data.update({
            'price': self.price,
            'volume': self.volume
        })
        return data
    
    @staticmethod
    def from_dict(data: Dict[str, Any]) -> 'BreakthroughSellRule':
        return BreakthroughSellRule(
            price=data.get('price', 0),
            volume=data.get('volume', 0),
            rule_id=data.get('id'),
            enabled=data.get('enabled', True),
            name=data.get('name')
        )

class RulesManager:
    """规则管理器"""
    
    @staticmethod
    def create_default_rules() -> List[Dict[str, Any]]:
        """创建默认规则列表（空列表）"""
        return []
    
    @staticmethod
    def add_rule(rules: List[Dict[str, Any]], rule: TradingRule) -> List[Dict[str, Any]]:
        """添加规则"""
        rules.append(rule.to_dict())
        return rules
    
    @staticmethod
    def remove_rule(rules: List[Dict[str, Any]], rule_id: str) -> List[Dict[str, Any]]:
        """删除规则"""
        return [r for r in rules if r.get('id') != rule_id]
    
    @staticmethod
    def update_rule(rules: List[Dict[str, Any]], rule_id: str, updates: Dict[str, Any]) -> List[Dict[str, Any]]:
        """更新规则"""
        for rule in rules:
            if rule.get('id') == rule_id:
                rule.update(updates)
                break
        return rules
    
    @staticmethod
    def get_rule(rules: List[Dict[str, Any]], rule_id: str) -> Dict[str, Any]:
        """获取规则"""
        for rule in rules:
            if rule.get('id') == rule_id:
                return rule
        return None
    
    @staticmethod
    def get_enabled_rules(rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """获取已启用的规则"""
        return [r for r in rules if r.get('enabled', True)]
    
    @staticmethod
    def get_rules_by_type(rules: List[Dict[str, Any]], rule_type: RuleType) -> List[Dict[str, Any]]:
        """按类型获取规则"""
        type_value = rule_type.value if isinstance(rule_type, RuleType) else rule_type
        return [r for r in rules if r.get('type') == type_value]

