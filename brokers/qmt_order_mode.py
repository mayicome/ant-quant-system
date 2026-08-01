# -*- coding: utf-8 -*-
"""兼容旧导入路径；实际定义在 utils.qmt_execution_config。"""
from utils.qmt_execution_config import (  # noqa: F401
    allow_qmt_client_auto_restart,
    get_qmt_mode,
    relax_xt_trader_health_check,
    requires_path_qmt,
    skip_external_quote_subscribe,
    use_builtin_order_execution,
    use_builtin_price_feed,
)
