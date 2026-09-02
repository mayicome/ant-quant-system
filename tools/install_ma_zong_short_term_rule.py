# -*- coding: utf-8 -*-
"""安装选股规则：马总短线选股逻辑

基于 tools/_rule_src_ma_zong_short_term.py（相对次日MA10 三点差异）：
  - 选股日当日涨停（非近10日回溯）
  - 前8日无大涨（非前10日）
  - 不判断布林上轨
  - 仅「满足条件」四项全真写入结果表

不覆盖「马总选股逻辑-次日MA10」「马总选股逻辑-盘后」。

用法:
  python tools/install_ma_zong_short_term_rule.py
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(__file__).resolve().parent / "_rule_src_ma_zong_short_term.py"
OUT_DIR = ROOT / "data" / "sector_rules"
NAME = "马总短线选股逻辑"


def main() -> None:
    code = SRC.read_text(encoding="utf-8")
    if "def select(" not in code:
        raise SystemExit("rule source missing select()")
    if "ma_zong_short_term" not in code:
        raise SystemExit("unexpected rule source (HOT_MODE)")
    if "PRIOR_LOOKBACK = 8" not in code:
        raise SystemExit("expected PRIOR_LOOKBACK = 8")
    if "LU_LOOKBACK = 1" not in code:
        raise SystemExit("expected LU_LOOKBACK = 1")
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
    print("enabled True — 未改动 马总选股逻辑-次日MA10 / 盘后")
    hist = ROOT / "history_data" / NAME
    hist.mkdir(parents=True, exist_ok=True)
    print("history dir", hist)


if __name__ == "__main__":
    raise SystemExit(main())
