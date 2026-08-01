# 策略引擎：根据策略配置与股票池生成「待生成任务」列表（不包含 task_id 等，由 task_builder 填充）

from .runner import run_strategy

__all__ = ["run_strategy"]
