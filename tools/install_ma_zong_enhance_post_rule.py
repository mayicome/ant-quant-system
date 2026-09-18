# -*- coding: utf-8 -*-
"""安装选股规则：增强选股逻辑-盘后（参照马总选股逻辑-盘后改满足条件）。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(__file__).resolve().parent / "_rule_src_ma_zong_enhance_post.py"
OUT_DIR = ROOT / "data" / "sector_rules"
NAME = "增强选股逻辑-盘后"


def main() -> None:
    code = SRC.read_text(encoding="utf-8")
    if "def select(" not in code:
        raise SystemExit("rule source missing select()")
    if "def _cond_board_band(" not in code:
        raise SystemExit("rule source missing _cond_board_band()")
    rid = str(uuid.uuid4())
    short = rid.split("-")[0]
    out = {
        "id": rid,
        "name": NAME,
        "enabled": True,
        "code": code,
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for old in OUT_DIR.glob("%s__*.json" % NAME):
        old.unlink()
        print("removed", old.name)
    path = OUT_DIR / ("%s__%s.json" % (NAME, short))
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", path)
    print("id", rid)
    print("enabled True — 未改动 马总选股逻辑-盘后")


if __name__ == "__main__":
    main()
