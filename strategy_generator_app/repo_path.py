# -*- coding: utf-8 -*-
"""
策略生成器路径：仓库根 = strategy_generator_app 的上一级（含 utils/、key_price_calculator.py 等）。

无论从项目根还是 strategy_generator_app 目录启动，均将仓库根置于 sys.path 最前，
避免 site-packages 中其它 utils 包抢占 import。
"""
from __future__ import annotations

import os
import sys


def strategy_generator_app_dir() -> str:
    return os.path.dirname(os.path.abspath(__file__))


def repo_root() -> str:
    """仓库根目录（strategy_generator_app 的上一级）。"""
    return os.path.dirname(strategy_generator_app_dir())


def _prepend_sys_path(path: str) -> None:
    if not path:
        return
    while path in sys.path:
        try:
            sys.path.remove(path)
        except ValueError:
            break
    sys.path.insert(0, path)


def ensure_repo_root_on_sys_path() -> str:
    """将仓库根插入 sys.path[0]，供 from utils.xxx、import key_price_calculator 使用。"""
    root = repo_root()
    _prepend_sys_path(root)
    return root


def ensure_app_on_sys_path() -> str:
    """将 strategy_generator_app 插入 sys.path，供 from config / engine 等使用。"""
    app = strategy_generator_app_dir()
    _prepend_sys_path(app)
    return app


def ensure_paths() -> tuple[str, str]:
    """先仓库根、再 app 目录（均在 path 前列）。"""
    app = strategy_generator_app_dir()
    root = repo_root()
    _prepend_sys_path(app)
    _prepend_sys_path(root)
    return root, app
