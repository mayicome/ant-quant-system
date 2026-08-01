# -*- coding: utf-8 -*-
"""
加载仓库根目录下的 utils/tick_data_cache.py（按运行时解析的绝对路径，不写死盘符）。

目录约定（与主程序一致）：
  <仓库根>/
    utils/tick_data_cache.py
    strategy_generator_app/
      backtest/tick_cache_loader.py  ← 本文件

仓库根 = strategy_generator_app 的上一级目录。
用 importlib 按文件路径加载，避免 site-packages 里其它 utils 包抢占 import。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from typing import Any

_MOD_NAME = "_ant_quant_tick_data_cache"
_CACHED_MOD: Any = None


def strategy_generator_app_dir() -> str:
    try:
        from repo_path import strategy_generator_app_dir as _app_dir
        return _app_dir()
    except ImportError:
        return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def repo_root() -> str:
    try:
        from repo_path import repo_root as _root
        return _root()
    except ImportError:
        return os.path.dirname(strategy_generator_app_dir())


def tick_cache_file_path() -> str:
    """<仓库根>/utils/tick_data_cache.py"""
    return os.path.join(repo_root(), "utils", "tick_data_cache.py")


def tick_data_cache_module() -> Any:
    """返回 tick_data_cache 模块对象（单例）。"""
    global _CACHED_MOD
    if _CACHED_MOD is not None:
        return _CACHED_MOD

    root = repo_root()
    mod_path = tick_cache_file_path()
    if not os.path.isfile(mod_path):
        raise ModuleNotFoundError(
            "缺少 tick 缓存模块：\n"
            f"  期望路径: {mod_path}\n"
            f"  （仓库根 = strategy_generator_app 的上一级: {root}）\n"
            "请将 utils/tick_data_cache.py 放在该仓库根下的 utils 目录中。"
        )

    try:
        from repo_path import ensure_repo_root_on_sys_path
        ensure_repo_root_on_sys_path()
    except ImportError:
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
        raise ImportError(f"无法创建 tick_data_cache 加载规格: {mod_path}")

    mod = importlib.util.module_from_spec(spec)
    sys.modules[_MOD_NAME] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:
        sys.modules.pop(_MOD_NAME, None)
        raise ImportError(f"执行 tick_data_cache 失败 ({mod_path}): {exc}") from exc

    _CACHED_MOD = mod
    return mod
