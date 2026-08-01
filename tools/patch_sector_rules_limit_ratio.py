#!/usr/bin/env python3
"""一次性补丁：更新 data/sector_rules/*.json 内嵌的 ST 涨跌幅逻辑。"""
import glob
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

import sector_rule_templates as srt

OLD = """def _limit_ratio(stock_code, stock_name):
    name = str(stock_name or "").upper()
    code = str(stock_code or "").strip()
    if "ST" in name:
        return 0.05
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("8", "4", "920")):
        return 0.30
    return 0.10"""

NEW = srt._EMBEDDED_LIMIT_RATIO_FUNC

REPLACEMENTS = [
    ("_is_limit_up_on_day(dd, trade_date, limit_ratio)", "_is_limit_up_on_day(dd, trade_date, stock_code, stock_name)"),
    ("_is_limit_up_on_day(dd, prev_d, limit_ratio)", "_is_limit_up_on_day(dd, prev_d, stock_code, stock_name)"),
    ("_is_limit_up_on_day(dd, d_chk, limit_ratio)", "_is_limit_up_on_day(dd, d_chk, stock_code, stock_name)"),
    ("    limit_ratio = _limit_ratio(stock_code, stock_name)\n    window_dates", "    window_dates"),
    (
        "    limit_ratio = _limit_ratio(stock_code, stock_name)\n    p_day = int(P)",
        "    p_day = int(P)",
    ),
    (
        "    limit_ratio = _limit_ratio(stock_code, stock_name)\n    n, m, l = int(N)",
        "    n, m, l = int(N)",
    ),
]


def main() -> None:
    for path in glob.glob(os.path.join(_ROOT, "data", "sector_rules", "*.json")):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        code = data.get("code", "")
        if OLD not in code:
            print("skip:", os.path.basename(path))
            continue
        code = code.replace(OLD, NEW)
        for a, b in REPLACEMENTS:
            code = code.replace(a, b)
        data["code"] = code
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("patched:", os.path.basename(path))


if __name__ == "__main__":
    main()
