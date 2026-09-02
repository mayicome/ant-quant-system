# -*- coding: utf-8 -*-
"""安装选股规则：马总选股逻辑-次日MA10

基于 tools/_rule_src_ma_zong_next_day_ma10.py（参考盘后字段骨架）：
  近10日最近涨停 + 涨停后日线未触 MA10 → 供次日开买。
  行业/概念软门槛：东财涨幅榜（industry_rank_/concept_rank_）行业前32或概念前8（同盘中逻辑2）。

不覆盖「马总选股逻辑-盘后」。

用法:
  python tools/install_ma_zong_next_day_ma10_rule.py
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(__file__).resolve().parent / "_rule_src_ma_zong_next_day_ma10.py"
OUT_DIR = ROOT / "data" / "sector_rules"
NAME = "马总选股逻辑-次日MA10"


def main() -> None:
    code = SRC.read_text(encoding="utf-8")
    if "def select(" not in code:
        raise SystemExit("rule source missing select()")
    if "ma_zong_next_day_ma10" not in code:
        raise SystemExit("unexpected rule source (HOT_MODE)")
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
    raise SystemExit(main())
