# -*- coding: utf-8 -*-
"""安装选股规则：马总选股逻辑1（基于 besttest MA空头字段骨架）。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(__file__).resolve().parent / "_rule_src_ma_zong_logic1.py"
OUT_DIR = ROOT / "data" / "sector_rules"
NAME = "马总选股逻辑-盘后"


def main() -> None:
    code = SRC.read_text(encoding="utf-8")
    if "def select(" not in code:
        raise SystemExit("rule source missing select()")
    # 去掉可能的模块级说明并不需要；整文件即规则 code
    rid = str(uuid.uuid4())
    short = rid.split("-")[0]
    out = {
        "id": rid,
        "name": NAME,
        "enabled": True,
        "code": code,
    }
    # 清理同名旧规则文件，避免 GUI 出现重复
    for old in OUT_DIR.glob("%s__*.json" % NAME):
        old.unlink()
        print("removed", old.name)
    path = OUT_DIR / ("%s__%s.json" % (NAME, short))
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", path)
    print("id", rid)
    print("enabled True — 原 besttest MA空头排列 规则未改")


if __name__ == "__main__":
    main()
