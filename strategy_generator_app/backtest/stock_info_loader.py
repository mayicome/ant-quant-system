# -*- coding: utf-8 -*-
"""
按仓库根目录绝对路径加载 utils/stock_info_manager.py，避免与其它 utils 包冲突。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any, Callable, Optional

_MOD_NAME = "_ant_quant_stock_info_manager"
_CACHED_GET_NAME: Optional[Callable[[str], str]] = None


def strategy_generator_app_dir() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def repo_root() -> str:
    return os.path.dirname(strategy_generator_app_dir())


def stock_info_file_path() -> str:
    return os.path.join(repo_root(), "utils", "stock_info_manager.py")


def get_stock_name_callable() -> Callable[[str], str]:
    """返回 get_stock_name(code) 函数；加载失败时抛出 ImportError。"""
    global _CACHED_GET_NAME
    if _CACHED_GET_NAME is not None:
        return _CACHED_GET_NAME

    root = repo_root()
    mod_path = stock_info_file_path()
    if not os.path.isfile(mod_path):
        raise ModuleNotFoundError(
            "缺少股票信息模块：\n"
            f"  期望路径: {mod_path}\n"
            f"  （仓库根 = strategy_generator_app 的上一级: {root}）\n"
            "请将 utils/stock_info_manager.py 与 data/all_a_stocks.csv 放在仓库根下。"
        )

    if root in sys.path:
        try:
            sys.path.remove(root)
        except ValueError:
            pass
    sys.path.insert(0, root)

    spec = importlib.util.spec_from_file_location(
        _MOD_NAME,
        mod_path,
        submodule_search_locations=[os.path.join(root, "utils")],
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"无法创建 stock_info_manager 加载规格: {mod_path}")

    mod: Any = importlib.util.module_from_spec(spec)
    sys.modules[_MOD_NAME] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:
        sys.modules.pop(_MOD_NAME, None)
        raise ImportError(f"执行 stock_info_manager 失败 ({mod_path}): {exc}") from exc

    fn = getattr(mod, "get_stock_name", None)
    if not callable(fn):
        raise ImportError(f"stock_info_manager 中未找到 get_stock_name: {mod_path}")

    _CACHED_GET_NAME = fn
    return fn
