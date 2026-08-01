#!/usr/bin/env python3
"""修复 sector_rules JSON 中 _is_limit_up_on_day / P-to-N 循环缺少 limit_ratio 的问题。"""
import glob
import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BROKEN_IS_LU_DAY = '''def _is_limit_up_on_day(dd, trade_date, stock_code, stock_name):
    sub = dd[dd["date"] == trade_date]'''

FIXED_IS_LU_DAY = '''def _is_limit_up_on_day(dd, trade_date, stock_code, stock_name):
    limit_ratio = _limit_ratio(stock_code, stock_name, trade_date)
    sub = dd[dd["date"] == trade_date]'''

BROKEN_P2N_LOOP = '''        if offset is None or offset < p_day or offset > n_day:
            continue

        row = dd[dd["date"] == trade_date].iloc[-1]'''

FIXED_P2N_LOOP = '''        if offset is None or offset < p_day or offset > n_day:
            continue

        limit_ratio = _limit_ratio(stock_code, stock_name, trade_date)

        row = dd[dd["date"] == trade_date].iloc[-1]'''

FIXED_MODE12_LOOP = '''        if trade_date >= last_trading_date:
            continue

        limit_ratio = _limit_ratio(stock_code, stock_name, trade_date)

        prev = dd[dd["date"] < trade_date]'''

BROKEN_MODE12_LOOP = '''        if trade_date >= last_trading_date:
            continue

        prev = dd[dd["date"] < trade_date]'''


def main() -> None:
    for path in glob.glob(os.path.join(_ROOT, "data", "sector_rules", "*.json")):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        code = data.get("code", "")
        changed = False
        if BROKEN_IS_LU_DAY in code and "    limit_ratio = _limit_ratio(stock_code, stock_name, trade_date)\n    sub = dd" not in code:
            code = code.replace(BROKEN_IS_LU_DAY, FIXED_IS_LU_DAY)
            changed = True
        if BROKEN_P2N_LOOP in code:
            code = code.replace(BROKEN_P2N_LOOP, FIXED_P2N_LOOP)
            changed = True
        if BROKEN_MODE12_LOOP in code:
            code = code.replace(BROKEN_MODE12_LOOP, FIXED_MODE12_LOOP)
            changed = True
        if changed:
            data["code"] = code
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            print("fixed:", os.path.basename(path))
        else:
            print("ok/skip:", os.path.basename(path))


if __name__ == "__main__":
    main()
