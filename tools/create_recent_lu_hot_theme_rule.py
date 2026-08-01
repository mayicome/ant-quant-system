# -*- coding: utf-8 -*-
"""注册选股规则：近10日涨停 + 十大热门板块或概念。"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sector_rule_templates import rule_code_recent_limit_up_in_hot_theme  # noqa: E402
from sector_stock_filter import load_sector_rules, save_single_sector_rule  # noqa: E402

RULE_NAME = "近10日涨停-十大热门板块或概念"


def main() -> None:
    code = rule_code_recent_limit_up_in_hot_theme(lookback_days=10, top_n=10)
    compile(code, f"<{RULE_NAME}>", "exec")
    existing = {str(r.get("name") or ""): r for r in load_sector_rules()}
    old = existing.get(RULE_NAME)
    rid = str(old.get("id") or uuid.uuid4()) if old else str(uuid.uuid4())
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
    print(f"{action}: {RULE_NAME}  id={rid}")
    names = [str(r.get("name")) for r in load_sector_rules()]
    assert RULE_NAME in names
    print("完成。请在选股程序规则列表中勾选启用后运行。")


if __name__ == "__main__":
    main()
