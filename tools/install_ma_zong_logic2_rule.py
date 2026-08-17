# -*- coding: utf-8 -*-
"""安装选股规则：马总选股逻辑2（盘中）。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(__file__).resolve().parent / "_rule_src_ma_zong_logic2.py"
OUT_DIR = ROOT / "data" / "sector_rules"
NAME = "马总选股逻辑-盘中"


def main() -> None:
    code = SRC.read_text(encoding="utf-8")
    if "def select(" not in code:
        raise SystemExit("rule source missing select()")
    rid = str(uuid.uuid4())
    short = rid.split("-")[0]
    out = {
        "id": rid,
        "name": NAME,
        "enabled": True,
        "code": code,
    }
    for old in OUT_DIR.glob("%s__*.json" % NAME):
        old.unlink()
        print("removed", old.name)
    path = OUT_DIR / ("%s__%s.json" % (NAME, short))
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", path)
    print("id", rid)
    print("enabled True")
    print("建议盘中用: python tools/run_ma_zong_logic2_intraday.py")


if __name__ == "__main__":
    main()
