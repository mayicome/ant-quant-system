#coding:gbk
"""路径：模块在仓库时用 __file__；拷到 QMT 后由 sync 注入 DATA_ROOT。"""
import os

try:
    _BUILTIN_DIR = os.path.dirname(os.path.abspath(__file__))
except NameError:
    _BUILTIN_DIR = ""
QMT_BUILTIN_DIR = _BUILTIN_DIR

# SYNC_DATA_ROOT_BEGIN
_DATA_ROOT = "D:\\" + "\u8682" + "\u8681" + "\u91cf" + "\u5316" + "\u7cfb" + "\u7edf" + "\\data"
# SYNC_DATA_ROOT_END


def _resolve_data_root():
    if _DATA_ROOT:
        return _DATA_ROOT
    parent_data = os.path.join(os.path.dirname(_BUILTIN_DIR), "data")
    if os.path.isfile(os.path.join(parent_data, "rules_armed.json")):
        return parent_data
    local_data = os.path.join(_BUILTIN_DIR, "data")
    return local_data


DATA_DIR = _resolve_data_root()
PROJECT_ROOT = os.path.dirname(DATA_DIR)
RULES_ARMED_PATH = os.path.join(DATA_DIR, "rules_armed.json")
RESULTS_PATH = os.path.join(DATA_DIR, "results.json")
