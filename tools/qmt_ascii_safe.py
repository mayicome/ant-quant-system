# -*- coding: utf-8 -*-
"""已废弃。请使用 tools/sync_qmt_gbk.py（保留中文绝对路径，生成 GBK 副本供 QMT 加载）。"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def main() -> None:
    script = Path(__file__).resolve().parent / "sync_qmt_gbk.py"
    print("qmt_ascii_safe 已废弃，改为调用 sync_qmt_gbk.py")
    subprocess.check_call([sys.executable, str(script)])


if __name__ == "__main__":
    main()
