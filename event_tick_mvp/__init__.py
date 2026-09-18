# -*- coding: utf-8 -*-
"""短线事件-tick 驱动策略 MVP-v1（独立研究包）。

规格：docs/短线事件tick驱动策略_MVPv1规格摘要.md

分层：
  layer2_event   — 日频收盘：超跌缩量 → watch_list
  layer3_filter  — 盘中 bar：流动性/一字板/行业 cap → buy_monitor_set
  layer4_lifecycle — 买入触发 + 持仓止盈止损 T+1 + 递延
  engine         — 按交易日编排回测（仅回放 watch_list ∪ 持仓）
"""
from __future__ import annotations

from event_tick_mvp.config import MvpConfig, default_scan_grids

__all__ = [
    "MvpConfig",
    "default_scan_grids",
]

__version__ = "0.1.0-mvp"
