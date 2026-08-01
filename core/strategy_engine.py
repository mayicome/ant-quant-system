import logging
from strategies.conservative_strategy import ConservativeStrategy
from strategies.moderate_strategy import ModerateStrategy
from strategies.aggressive_strategy import AggressiveStrategy
from utils.logger import Logger

class StrategyEngine:
    def __init__(self, mode='live', logger=None):
        # 如果传入了 logger，直接使用它
        if logger:
            self.logger = logger
        else:
            # 否则创建新的 logger
            self.logger = Logger(mode=mode)
            
        self.strategies = {
            '规则任务': ModerateStrategy,
        }
        
    def create_strategy(self, strategy_type, task_info, log_pipe):
        """创建策略实例"""
        try:
            # 将策略名称转换为完整的策略类名（仅保留规则任务）
            strategy_map = {
                '规则任务': 'ModerateStrategy',
            }
            
            strategy_class = self.strategies.get(strategy_type)
            if not strategy_class:
                raise ValueError(f"未知的策略类型: {strategy_type}")
            
            # 确保策略类已正确导入
            task_info['strategy_name'] = strategy_map.get(strategy_type)  # 添加完整的策略类名到任务信息中
            strategy = strategy_class(task_info, log_pipe)
            self.logger.info(f"[{task_info['stock_code']}]创建策略: {strategy_type}")
            return strategy
            
        except Exception as e:
            log_pipe.send(f"创建策略失败: {str(e)}")
            raise
