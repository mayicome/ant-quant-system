# -*- coding: utf-8 -*-
"""安装选股规则：布林%b回落选股

基于 tools/_rule_src_bb_pctb_pullback.py：
  硬条件：%b<=0.05 + 除权过滤
    + MA10归一斜率>=-0.004
    + 流通市值<80亿
    + 最佳板块或概念排名∈[1,50]
  选股日 < 2026-01-01 优先读 data/daily_full。

用法:
  python tools/install_bb_pctb_pullback_rule.py
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(__file__).resolve().parent / "_rule_src_bb_pctb_pullback.py"
OUT_DIR = ROOT / "data" / "sector_rules"
NAME = "布林%b回落选股"


def main() -> None:
    code = SRC.read_text(encoding="utf-8")
    if "def select(" not in code:
        raise SystemExit("rule source missing select()")
    if "bb_pctb_pullback" not in code:
        raise SystemExit("unexpected rule source (HOT_MODE)")
    if "PCTB_MAX = 0.05" not in code:
        raise SystemExit("expected PCTB_MAX = 0.05")
    if "MA10_SLOPE_NORM_MIN = -0.004" not in code:
        raise SystemExit("expected MA10_SLOPE_NORM_MIN = -0.004")
    if "MAX_FLOAT_MV_YI = 80.0" not in code:
        raise SystemExit("expected MAX_FLOAT_MV_YI = 80.0")
    if "BOARD_RANK_HI = 50" not in code:
        raise SystemExit("expected BOARD_RANK_HI = 50")
    if "MA10_SLOPE_DAYS = 5" not in code:
        raise SystemExit("expected MA10_SLOPE_DAYS = 5")
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
    print("enabled True")
    hist = ROOT / "history_data" / NAME
    hist.mkdir(parents=True, exist_ok=True)
    print("history dir", hist)


if __name__ == "__main__":
    raise SystemExit(main())
