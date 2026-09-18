# -*- coding: utf-8 -*-
"""今开/最高/最低：竞价不写；开盘后跟 tick，可纠正。"""
from __future__ import annotations

import os
import sys
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "qmt_builtin", "src"))

from ant_rules_io import empty_results, update_price_snapshot  # noqa: E402


def test_skip_ohlc_in_auction():
    results = empty_results(trade_date="20260915")
    row = {"open": 27.72, "high": 28.10, "low": 25.0, "lastPrice": 25.5, "lastClose": 25.73}
    with mock.patch("ant_rules_io._in_call_auction_indicative_window", return_value=True):
        assert update_price_snapshot(results, "601231.SH", 25.5, "09:20:00", tick_row=row)
    b = results["stocks"]["601231.SH"]
    assert float(b.get("today_open") or 0) == 0.0
    assert float(b.get("today_high") or 0) == 0.0
    assert float(b.get("today_low") or 0) == 0.0
    assert abs(float(b["last_price"]) - 25.5) < 1e-9


def test_follow_tick_and_allow_correct():
    results = empty_results(trade_date="20260915")
    dirty = {"open": 27.72, "high": 28.10, "low": 25.0, "lastPrice": 25.5, "lastClose": 25.73}
    with mock.patch("ant_rules_io._in_call_auction_indicative_window", return_value=False):
        update_price_snapshot(results, "601231.SH", 25.5, "09:26:00", tick_row=dirty)
        b = results["stocks"]["601231.SH"]
        assert abs(float(b["today_open"]) - 27.72) < 1e-9
        assert abs(float(b["today_high"]) - 28.10) < 1e-9
        # 下一笔正确 tick：应纠正，不锁死
        good = {"open": 25.76, "high": 26.38, "low": 25.15, "lastPrice": 25.45, "lastClose": 25.73}
        update_price_snapshot(results, "601231.SH", 25.45, "10:00:00", tick_row=good)
    b = results["stocks"]["601231.SH"]
    assert abs(float(b["today_open"]) - 25.76) < 1e-9, b
    assert abs(float(b["today_high"]) - 26.38) < 1e-9, b
    assert abs(float(b["today_low"]) - 25.15) < 1e-9, b


if __name__ == "__main__":
    test_skip_ohlc_in_auction()
    test_follow_tick_and_allow_correct()
    print("ok")
