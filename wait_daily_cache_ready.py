# -*- coding: utf-8 -*-
"""盘后下载日线到 data/daily_cache。

东财优先，失败再用腾讯、同花顺。不再等待 QMT 写日线。
15:00 前补上一交易日；15:00 起补当天。非交易日补最近一个交易日。
供 run_all_if_trading_day.py / 盘后批跑在选股前调用。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.append_daily_cache_external import append_trade_day  # noqa: E402
from utils.post_market_pipeline import resolve_asof_day  # noqa: E402
from utils.trading_day import previous_tradeday  # noqa: E402


def main() -> int:
    day = resolve_asof_day()
    prev = previous_tradeday(day)
    print(
        f"[日线] 行情导出为主，缺的再向公开行情要 目标日={day.isoformat()} "
        f"上一交易日={prev.isoformat()}",
        flush=True,
    )
    return append_trade_day(day.isoformat(), prev.isoformat())


if __name__ == "__main__":
    raise SystemExit(main())
