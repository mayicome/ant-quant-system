# -*- coding: utf-8 -*-
"""注册：东财热门 any-tag 全量（关闭夹档/涨停/均线排列/市值硬过滤）。"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sector_rule_templates import rule_code_em_today_hot_elig_band  # noqa: E402
from sector_stock_filter import load_sector_rules, save_single_sector_rule  # noqa: E402

NAME = "东财热门-anytag全量-无个股过滤"


def main() -> None:
    code = rule_code_em_today_hot_elig_band(
        elig_lo=1,
        elig_hi=30,
        top_n=50,
        rs_top_k=50,
        rs_lo=1,
        rs_hi=50,
        rs_lookback=10,
        min_members=10,
        ma_gap_lo=0.005,
        ma_gap_hi=0.02,
        min_float_mv_yi=120.0,
        any_tag=True,
        apply_stock_filters=False,
    )
    # 与现网东财热门一致：组内 RS 前 1/2
    code = code.replace("RS_TOP_FRAC_DEN = 3", "RS_TOP_FRAC_DEN = 2")
    code = (
        code.replace("前1/3", "前1/2")
        .replace("ceil(样本/3)", "ceil(样本/2)")
        .replace("frac1of3", "frac1of2")
    )
    compile(code, f"<{NAME}>", "exec")
    assert "APPLY_STOCK_FILTERS = False" in code
    assert "ANY_TAG = True" in code
    assert "if APPLY_STOCK_FILTERS:" in code

    existing = {str(r.get("name") or ""): dict(r) for r in load_sector_rules()}
    if NAME in existing:
        rule = existing[NAME]
        rule["code"] = code
        rule["enabled"] = True
    else:
        rule = {
            "id": str(uuid.uuid4()),
            "name": NAME,
            "enabled": True,
            "code": code,
        }
    save_single_sector_rule(rule)
    names = {str(r.get("name")) for r in load_sector_rules()}
    assert NAME in names
    print(f"OK: {NAME}")
    print("  ANY_TAG=True  APPLY_STOCK_FILTERS=False  ELIG[1,30]  RS前1/2")
    print("  已写入 data/sector_rules/（原「东财热门」未改）")


if __name__ == "__main__":
    main()
