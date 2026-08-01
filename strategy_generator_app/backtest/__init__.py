# 策略生成系统 — 日频多标的回测
# 复用 run(codes, prices, get_name, account, params) 接口，按历史日线逐日跑策略并模拟成交。

from .data_provider import get_historical_prices_for_date
from .engine import run_backtest, run_backtest_segmented
from .metrics import compute_metrics
from .preflight import backtest_preflight_hint_lines, run_backtest_preflight

__all__ = [
    "get_historical_prices_for_date",
    "run_backtest",
    "run_backtest_segmented",
    "compute_metrics",
    "backtest_preflight_hint_lines",
    "run_backtest_preflight",
]
