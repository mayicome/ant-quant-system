# -*- coding: utf-8 -*-
"""删除一次性 qfq 补齐脚本（QMT 目录 + 仓库 qmt_builtin）。"""
from __future__ import annotations

import configparser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAMES = (
    "蚂蚁量化_qfq补齐.py",
    "ant_daily_qfq_backfill_once.py",
)


def _qmt_dir() -> Path:
    cp = configparser.ConfigParser()
    cp.read(ROOT / "data" / "config.ini", encoding="utf-8")
    raw = cp.get("qmt_builtin", "qmt_python_dir", fallback="").strip()
    if not raw:
        raise SystemExit("missing qmt_python_dir in config.ini")
    return Path(raw.replace("/", "\\"))


def main() -> int:
    removed = []
    for base in (_qmt_dir(), ROOT / "qmt_builtin", ROOT / "qmt_builtin" / "src"):
        if not base.is_dir():
            continue
        for name in NAMES:
            p = base / name
            if p.is_file():
                p.unlink()
                removed.append(str(p))
    if removed:
        print("removed:")
        for r in removed:
            print(" ", r)
    else:
        print("nothing to remove")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
