#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.elastic_sell import compute_best_sell_fallback, load_elastic_global_config


def test_room_blend_formula():
    cfg = load_elastic_global_config(force_reload=True)
    assert cfg.dynamic_thresholds == 2
    pre, lu = 10.0, 11.0
    # 7.5% tier @ +8.7%
    eff, fb = compute_best_sell_fallback(
        10.87,
        trigger_price=10.75,
        drop_percent=5.0,
        room_blend_start=1.5,
        limit_up=lu,
        pre_close=pre,
        dynamic_thresholds=2,
    )
    assert abs(eff - 3.2) < 0.05
    assert abs((fb / pre - 1) * 100 - 5.22) < 0.1
    # near board @ +9.0%
    eff2, fb2 = compute_best_sell_fallback(
        10.90,
        trigger_price=10.75,
        drop_percent=5.0,
        room_blend_start=1.5,
        limit_up=lu,
        pre_close=pre,
        dynamic_thresholds=2,
    )
    assert abs(eff2 - 0.5) < 0.01
    assert abs((fb2 / pre - 1) * 100 - 8.45) < 0.05
    print("elastic_sell tests ok")


if __name__ == "__main__":
    test_room_blend_formula()
