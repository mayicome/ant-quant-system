# -*- coding: utf-8 -*-
"""注册选股规则：主力净流入 A档 / B档。"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sector_rule_templates import (  # noqa: E402
    rule_code_main_inflow_tier_a,
    rule_code_main_inflow_tier_b,
)
from sector_stock_filter import load_sector_rules, save_single_sector_rule  # noqa: E402

RULES = [
    ("主力净流入-A档", rule_code_main_inflow_tier_a),
    ("主力净流入-B档", rule_code_main_inflow_tier_b),
]


def main() -> None:
    existing = {str(r.get("name") or ""): r for r in load_sector_rules()}
    for name, factory in RULES:
        code = factory()
        compile(code, f"<{name}>", "exec")
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
        print(f"{action}: {name}  id={rid}")
    print("完成。请在选股程序规则列表中勾选启用后运行。")


if __name__ == "__main__":
    main()
