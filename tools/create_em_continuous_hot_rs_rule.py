# -*- coding: utf-8 -*-
"""注册选股规则：东财连续2日合格 Top50（成分≥30）热门 + 组内近10日 RS 前20。"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sector_rule_templates import rule_code_em_continuous_hot_rs_top20  # noqa: E402
from sector_stock_filter import load_sector_rules, save_single_sector_rule  # noqa: E402

RULE_NAME = "东财热门-连续2日Top50-组内RS前20"


def main() -> None:
    code = rule_code_em_continuous_hot_rs_top20(
        top_n=50, rs_top_k=20, rs_lookback=10, min_members=30
    )
    compile(code, f"<{RULE_NAME}>", "exec")
    existing = {str(r.get("name") or ""): r for r in load_sector_rules()}
    old = existing.get(RULE_NAME)
    rid = str(old.get("id") or uuid.uuid4()) if old else str(uuid.uuid4())
    # 新规则默认启用；已存在则保留用户勾选，但本工具首次创建时 enabled=True
    enabled = bool(old.get("enabled", True)) if old else True
    save_single_sector_rule(
        {
            "id": rid,
            "name": RULE_NAME,
            "enabled": enabled,
            "code": code,
        }
    )
    action = "更新" if old else "新增"
    print(f"{action}: {RULE_NAME}  id={rid}  enabled={enabled}")
    names = [str(r.get("name")) for r in load_sector_rules()]
    assert RULE_NAME in names
    print("完成。规则已写入 data/sector_rules/。")


if __name__ == "__main__":
    main()
