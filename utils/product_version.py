# -*- coding: utf-8 -*-
"""蚂蚁量化系统统一产品版本（启动器 / 交易 / 选股 / 策略生成共用）。

- VERSION_BASE：主版本，需发大版本时手工改
- VERSION_SUFFIX：提交时由 git hook 自动更新（YYYYMMDD.序号）
- 展示形态：V{VERSION_BASE}.{VERSION_SUFFIX}，例如 V4.2.20260928.01
"""
from __future__ import annotations

PRODUCT_NAME = "蚂蚁量化系统"

# 主版本（手工维护）
VERSION_BASE = "4.2"

# 提交后缀（.githooks/pre-commit → scripts/bump_product_version_suffix.py 自动改）
VERSION_SUFFIX = "20261003.01"


def display_version() -> str:
    base = (VERSION_BASE or "").strip()
    suffix = (VERSION_SUFFIX or "").strip()
    if not base:
        return "V?"
    if not suffix:
        return f"V{base}"
    return f"V{base}.{suffix}"


def version_label() -> str:
    """用于「版本号：V…」文案。"""
    return f"版本号：{display_version()}"


def window_title(app_name: str) -> str:
    """子系统窗口标题：名称 + 统一版本号。"""
    name = (app_name or "").strip() or PRODUCT_NAME
    return f"{name} {display_version()}"
