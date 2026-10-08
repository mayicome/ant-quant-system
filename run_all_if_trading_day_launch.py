# -*- coding: utf-8 -*-
"""ASCII bat 用的启动器：进入工程目录后以 --scheduled 打开盘后批跑 GUI。"""
import os
import runpy
import sys

ROOT = r"D:\蚂蚁量化系统"
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 计划任务 bat 先进这里：先关 Quick Edit，再加载 GUI（避免误点黑窗冻结）
try:
    from utils.win_console import disable_quick_edit

    disable_quick_edit()
except Exception:
    pass

sys.argv = ["run_all_if_trading_day_gui.py", "--scheduled"] + sys.argv[1:]
runpy.run_path(
    os.path.join(ROOT, "run_all_if_trading_day_gui.py"),
    run_name="__main__",
)
