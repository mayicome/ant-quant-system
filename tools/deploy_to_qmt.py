# -*- coding: utf-8 -*-
"""把 qmt_builtin/*.py（GBK）复制到大 QMT 策略 python 目录。"""
from __future__ import annotations

import configparser
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "qmt_builtin"
CONFIG = ROOT / "data" / "config.ini"

MODULE_FILES = (
    "ant_qmt_paths.py",
    "ant_rules_io.py",
    "ant_shadow_strategy.py",
    "ant_tick_runner.py",
    "ant_true_breakthrough_lite.py",
    "ant_elastic_sell_lite.py",
    "ant_elastic_buy_lite.py",
    "ant_daily_sync_runner.py",
    "ant_after_hours_rank_runner.py",
    "ant_sector_sync_runner.py",
    "ant_data_sync_request.py",
    "ant_tick_cache_io.py",
    "ant_tick_full_sync_runner.py",
    "ant_server_chan.py",
    "ant_account_snapshot.py",
    "ant_passorder.py",
    "ant_position_entry_dates.py",
    "ant_filled_legs.py",
    "ant_cancel_request.py",
)
# 不复制 rules_io.py 等兼容层；仅主程序仓库内使用
ENTRY_FILE = "蚂蚁量化规则.py"


def _qmt_python_dir() -> Path:
    cp = configparser.ConfigParser()
    cp.read(CONFIG, encoding="utf-8")
    raw = cp.get("qmt_builtin", "qmt_python_dir", fallback="").strip()
    if not raw:
        raise SystemExit("set [qmt_builtin] qmt_python_dir in data/config.ini")
    return Path(raw.replace("/", "\\"))


def main() -> None:
    dst = _qmt_python_dir()
    if not dst.is_dir():
        raise SystemExit("qmt_python_dir not found: " + str(dst))

    for name in MODULE_FILES:
        src = SRC / name
        if not src.is_file():
            raise SystemExit("missing " + str(src) + " (run sync_qmt_gbk.py first)")
        shutil.copy2(src, dst / name)
        print("copied", dst / name)

    entry_src = SRC / ENTRY_FILE
    if not entry_src.is_file():
        raise SystemExit("missing " + str(entry_src))
    shutil.copy2(entry_src, dst / ENTRY_FILE)
    print("copied", dst / ENTRY_FILE)

    print("done. restart strategy in model trading")


if __name__ == "__main__":
    main()
