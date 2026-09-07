# -*- coding: utf-8 -*-
"""移除蚂蚁量化规则中的后复权批量注入块，并 sync+deploy。

用法：
  python tools/remove_hfq_bulk_inject.py
  python tools/remove_hfq_bulk_inject.py --no-deploy
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ENTRY = ROOT / "qmt_builtin" / "src" / "蚂蚁量化规则.py"
PATTERN = re.compile(
    r"[ \t]*# HFQ_BULK_INJECT_BEGIN\n.*?[ \t]*# HFQ_BULK_INJECT_END\n?",
    re.DOTALL,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-deploy", action="store_true")
    args = ap.parse_args()
    text = SRC_ENTRY.read_text(encoding="utf-8")
    new, n = PATTERN.subn("", text)
    if n == 0:
        print("no inject markers found in", SRC_ENTRY)
    else:
        SRC_ENTRY.write_text(new, encoding="utf-8")
        print("removed", n, "inject blocks from", SRC_ENTRY.name)

    # bump version note
    if 'ENTRY_VERSION = "20260907.01"' in new:
        new2 = new.replace(
            'ENTRY_VERSION = "20260907.01"',
            'ENTRY_VERSION = "20260907.03"',
            1,
        )
        SRC_ENTRY.write_text(new2, encoding="utf-8")
        print("ENTRY_VERSION -> 20260907.03")
    elif 'ENTRY_VERSION = "20260907.02"' in new:
        new2 = new.replace(
            'ENTRY_VERSION = "20260907.02"',
            'ENTRY_VERSION = "20260907.03"',
            1,
        )
        SRC_ENTRY.write_text(new2, encoding="utf-8")
        print("ENTRY_VERSION -> 20260907.03")

    py = sys.executable
    subprocess.check_call([py, str(ROOT / "tools" / "sync_qmt_gbk.py")], cwd=str(ROOT))
    if not args.no_deploy:
        subprocess.check_call([py, str(ROOT / "tools" / "deploy_to_qmt.py")], cwd=str(ROOT))
    print("done. restart strategy in model trading to unload ant_hfq_bulk_runner if desired")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
