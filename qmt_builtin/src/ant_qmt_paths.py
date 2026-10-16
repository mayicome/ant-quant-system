# -*- coding: utf-8 -*-
"""路径：模块在仓库时用 __file__；拷到 QMT 后由 sync 注入 DATA_ROOT。"""
import os

try:
    _BUILTIN_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _BUILTIN_DIR = ""
QMT_BUILTIN_DIR = _BUILTIN_DIR

# SYNC_DATA_ROOT_BEGIN
_DATA_ROOT = ""
# SYNC_DATA_ROOT_END


def _resolve_data_root():
    """部署进 QMT 后用 sync 写好的 _DATA_ROOT。源码布局按目录名推仓库 data，不探测任何文件。"""
    if _DATA_ROOT:
        return _DATA_ROOT
    here = _BUILTIN_DIR
    parent = os.path.dirname(here) if here else ""
    if os.path.basename(here).lower() == "src" and parent:
        return os.path.join(os.path.dirname(parent), "data")
    if parent:
        return os.path.join(parent, "data")
    return os.path.join(here, "data") if here else "data"


DATA_DIR = _resolve_data_root()
PROJECT_ROOT = os.path.dirname(DATA_DIR)
RULES_ARMED_PATH = os.path.join(DATA_DIR, "rules_armed.json")
RESULTS_PATH = os.path.join(DATA_DIR, "results.json")
