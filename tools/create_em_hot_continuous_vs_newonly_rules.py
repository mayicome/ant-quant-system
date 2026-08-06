# -*- coding: utf-8 -*-
"""注册两套对照选股规则：连续2日热门 vs 仅今日热门（同 RS20 + 均线过滤）。"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sector_rule_templates import rule_code_em_hot_rs_ma_band  # noqa: E402
from sector_stock_filter import load_sector_rules, save_single_sector_rule  # noqa: E402

RULES = (
    (
        "东财热门-连续2日Top50-RS20-夹档0.5to2-无涨停-MA5ltMA10lt20",
        "continuous",
    ),
    (
        "东财热门-仅今日Top50-RS20-夹档0.5to2-无涨停-MA5ltMA10lt20",
        "new_only",
    ),
)


def _upsert(name: str, hot_mode: str) -> None:
    code = rule_code_em_hot_rs_ma_band(
        hot_mode=hot_mode,
        top_n=50,
        rs_top_k=20,
        rs_lookback=10,
        min_members=30,
        ma_gap_lo=0.005,
        ma_gap_hi=0.02,
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
    print(f"{action}: {name}  id={rid}  enabled={enabled}  hot_mode={hot_mode}")


def main() -> None:
    for name, mode in RULES:
        _upsert(name, mode)
    names = {str(r.get("name")) for r in load_sector_rules()}
    for name, _ in RULES:
        assert name in names, name
    print("完成。规则已写入 data/sector_rules/。")
    print("跑法：选股界面各勾选一条，分别导出七月结果后用同一策略回测。")


if __name__ == "__main__":
    main()
