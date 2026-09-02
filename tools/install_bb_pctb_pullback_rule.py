# -*- coding: utf-8 -*-
"""安装选股规则：布林%b回落选股

基于 tools/_rule_src_bb_pctb_pullback.py：
  流通市值 80~800 亿 + 涨幅榜最好名次 21~100
  + %b 买点 A（近5日曾%b<0且今日>=0.05）或 B（今日<=0.04且T/T-1不再创新低）
  + MA10 不持续向下。
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
    if "PCTB_A_MIN = 0.05" not in code:
        raise SystemExit("expected PCTB_A_MIN = 0.05")
    if "PCTB_B_MAX = 0.04" not in code:
        raise SystemExit("expected PCTB_B_MAX = 0.04")
    if "MA10_SLOPE_NORM_MIN = -0.008" not in code:
        raise SystemExit("expected MA10_SLOPE_NORM_MIN = -0.008")
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
