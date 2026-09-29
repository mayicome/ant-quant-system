# -*- coding: utf-8 -*-
"""策略 / 选股规则安装脚本统一闸门。

产品约定：策略与选股规则都是平台上的独立插件，只通过 JSON 导入/导出或复制同步，
不得用 tools/install_* / create_* 脚本自动写入。

紧急需要跑旧安装脚本时（不推荐）：
  set FORCE_STRATEGY_INSTALL=1   # 策略
  set FORCE_RULE_INSTALL=1       # 选股规则
  python tools/install_xxx.py
"""
from __future__ import annotations

import os
import sys

_MSG_STRATEGY = """\
策略安装脚本已停用。

蚂蚁量化按「平台 + 插件」工作：
  - 策略只存在于本机 strategy_generator_app/config/strategies/*.json
  - 同步方式：策略生成器「导入/导出」，或直接复制 JSON
  - 禁止用 install_*_strategy.py 自动生成/覆盖策略

改策略逻辑：直接改对应 JSON 的 strategy_code，或在策略生成器里编辑。
"""

_MSG_RULE = """\
选股规则安装脚本已停用。

蚂蚁量化按「平台 + 插件」工作：
  - 选股规则只存在于本机 data/sector_rules/*.json
  - 同步方式：选股系统「导入规则/导出规则」，或直接复制 JSON
  - 禁止用 install_*_rule.py / create_*_rule.py 自动生成/覆盖规则

改规则逻辑：在选股系统里编辑并保存，或直接改对应 JSON。
"""


def refuse_strategy_install(script_name: str = "") -> None:
    if os.environ.get("FORCE_STRATEGY_INSTALL", "").strip() == "1":
        return
    name = (script_name or "").strip()
    if name:
        print(f"[{name}]")
    print(_MSG_STRATEGY.rstrip())
    raise SystemExit(2)


def refuse_rule_install(script_name: str = "") -> None:
    if os.environ.get("FORCE_RULE_INSTALL", "").strip() == "1":
        return
    name = (script_name or "").strip()
    if name:
        print(f"[{name}]")
    print(_MSG_RULE.rstrip())
    raise SystemExit(2)


if __name__ == "__main__":
    refuse_rule_install(os.path.basename(sys.argv[0] if sys.argv else ""))
