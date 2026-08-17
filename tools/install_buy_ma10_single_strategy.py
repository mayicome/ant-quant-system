# -*- coding: utf-8 -*-
"""把 tools/_strategy_src_buy_ma10_single.py 写回「买：跌MA10」策略代码（保留股票池/参数）。"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = Path(__file__).resolve().parent / "_strategy_src_buy_ma10_single.py"
CFG = ROOT / "strategy_generator_app" / "config" / "strategies" / "strategy_ma10_single.json"


def main() -> None:
    code = SRC.read_text(encoding="utf-8")
    if "def run(" not in code:
        raise SystemExit("strategy source missing run()")
    compile(code, str(SRC), "exec")
    cfg = json.loads(CFG.read_text(encoding="utf-8"))
    cfg["strategy_code"] = code
    sp = dict(cfg.get("strategy_params") or {})
    sp["skip_if_already_touched_ma"] = bool(sp.get("skip_if_already_touched_ma", False))
    sp["scan_already_touched_ma_on_import"] = bool(
        sp.get("scan_already_touched_ma_on_import", True)
    )
    cfg["strategy_params"] = sp
    CFG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("updated", CFG)
    print("skip_if_already_touched_ma =", sp.get("skip_if_already_touched_ma"))
    print("scan_already_touched_ma_on_import =", sp.get("scan_already_touched_ma_on_import"))


if __name__ == "__main__":
    main()
