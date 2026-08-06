# -*- coding: utf-8 -*-
"""注册方案A热门档位三档对照：头[1,15] / 中[16,35] / 尾[36,50]。"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sector_rule_templates import rule_code_em_today_hot_elig_band  # noqa: E402
from sector_stock_filter import load_sector_rules, save_single_sector_rule  # noqa: E402

RULES = (
    (
        "东财热门-今日Top50-头档Elig1to15-RS20-夹档-无涨停-MA5ltMA10lt20-流通ge120",
        1,
        15,
    ),
    (
        "东财热门-今日Top50-中档Elig16to35-RS20-夹档-无涨停-MA5ltMA10lt20-流通ge120",
        16,
        35,
    ),
    (
        "东财热门-今日Top50-尾档Elig36to50-RS20-夹档-无涨停-MA5ltMA10lt20-流通ge120",
        36,
        50,
    ),
)

# 旧两档命名（若曾注册，保留但本脚本以三档为准；中档为新增）
LEGACY_TWO_TIER = (
    "东财热门-今日Top50-头档Elig1to15-RS20-夹档-无涨停-MA5ltMA10lt20-流通ge120",
    "东财热门-今日Top50-尾档Elig36to50-RS20-夹档-无涨停-MA5ltMA10lt20-流通ge120",
)


def _upsert(name: str, elig_lo: int, elig_hi: int) -> None:
    code = rule_code_em_today_hot_elig_band(
        elig_lo=elig_lo,
        elig_hi=elig_hi,
        top_n=50,
        rs_top_k=20,
        rs_lookback=10,
        min_members=30,
        ma_gap_lo=0.005,
        ma_gap_hi=0.02,
        min_float_mv_yi=120.0,
    )
    compile(code, f"<{name}>", "exec")
    existing = {str(r.get("name") or ""): r for r in load_sector_rules()}
    old = existing.get(name)
    rid = str(old.get("id") or uuid.uuid4()) if old else str(uuid.uuid4())
    enabled = bool(old.get("enabled", True)) if old else True
    save_single_sector_rule(
        {
            "id": rid,
            "name": name,
            "enabled": enabled,
            "code": code,
        }
    )
    action = "更新" if old else "新增"
    print(f"{action}: {name}  id={rid}  elig=[{elig_lo},{elig_hi}]")


def main() -> None:
    for name, lo, hi in RULES:
        _upsert(name, lo, hi)
    names = {str(r.get("name")) for r in load_sector_rules()}
    for name, _, _ in RULES:
        assert name in names, name
    print("完成。三档规则已写入 data/sector_rules/。")
    print("底座：今日合格Top50 + RS20 + 夹档0.5~2% + 近10日无涨停 + MA5<MA10<MA20 + 流通≥120亿")
    print("跑法：各勾选一条分别选七月 → 同一策略回测后三档对比。")


if __name__ == "__main__":
    main()
