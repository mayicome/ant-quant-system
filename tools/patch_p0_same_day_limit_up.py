# -*- coding: utf-8 -*-
"""Allow P/N=0 in 涨停的第P到N天-严多头 (day 0 = limit-up day)."""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(r"d:\蚂蚁量化系统")
sys.path.insert(0, str(ROOT))

from sector_stock_filter import load_sector_rules, save_single_sector_rule  # noqa: E402

RULE_NAME = "涨停的第P到N天-严多头"


def main() -> None:
    rules = [r for r in load_sector_rules() if r.get("name") == RULE_NAME]
    assert len(rules) == 1, rules
    rule = dict(rules[0])
    code = rule["code"]

    old_header = (
        "P = 0  # 选股日 = 锚定涨停后第 P 个交易日（含）；第1日=涨停后首个交易日\n"
        "N = 0  # 选股日 = 锚定涨停后第 N 个交易日（含）上限；须 P <= N"
    )
    new_header = (
        "P = 0  # 选股日相对锚定涨停的交易日偏移（含）；0=涨停当天；1=涨停后首个交易日\n"
        "N = 0  # 上限（含）；须 0 <= P <= N"
    )
    assert old_header in code, "header not found"

    old_offset = '''def _post_lu_trading_offset(all_dates, trade_date, as_of_effective):
    try:
        idx_lu = all_dates.index(trade_date)
        idx_as = all_dates.index(as_of_effective)
    except ValueError:
        return None
    if idx_as <= idx_lu:
        return None
    return idx_as - idx_lu'''

    new_offset = '''def _post_lu_trading_offset(all_dates, trade_date, as_of_effective):
    try:
        idx_lu = all_dates.index(trade_date)
        idx_as = all_dates.index(as_of_effective)
    except ValueError:
        return None
    if idx_as < idx_lu:
        return None
    # 0=涨停当天；1=涨停后首个交易日
    return idx_as - idx_lu'''
    assert old_offset in code, "offset fn not found"

    old_candidates = "    anchor_candidates = all_dates[scan_start:idx_as][::-1]"
    new_candidates = (
        "    # 含 as_of 自身，以支持 P/N=0（选股日=涨停当天）\n"
        "    anchor_candidates = all_dates[scan_start : idx_as + 1][::-1]"
    )
    assert old_candidates in code, "candidates not found"

    old_after = '''        after = dd[(dd["date"] > trade_date) & (dd["date"] <= as_of_eff)]
        if after.empty:
            continue

        max_allowed = limit_up_price * (1.0 + limit_ratio * 0.5)
        max_high_after = float(after["close"].max())
        signal_date = None
        has_obvious_new_high = max_high_after > max_allowed + nh_tol

        if REQUIRE_OBVIOUS_NEW_HIGH is not None:
            if REQUIRE_OBVIOUS_NEW_HIGH:
                if not has_obvious_new_high:
                    continue
                nh = after.sort_values("date")
                nh = nh[nh["close"] > max_allowed + nh_tol]
                if nh.empty:
                    continue
                signal_date = nh.iloc[0]["date"]
            elif has_obvious_new_high:
                continue

        after_ma = after.copy()
        after_ma["MA_MIN_5_10"] = after_ma[["MA5", "MA10"]].min(axis=1, skipna=True)
        after_ma["MA_MIN_20_120"] = after_ma[["MA20", "MA30", "MA60", "MA120"]].min(axis=1, skipna=True)

        bad_short = after_ma["close"] < (after_ma["MA_MIN_5_10"] - 0.01)
        bad_long = after_ma["close"] < (after_ma["MA_MIN_20_120"] - 0.01)
        breaks_ma = bad_short | bad_long
        ma_support_held = not breaks_ma.any()
        if REQUIRE_MA_SUPPORT_AFTER is not None:
            if REQUIRE_MA_SUPPORT_AFTER:
                if bad_short.any():
                    continue
                if bad_long.any():
                    continue
            elif not breaks_ma.any():
                continue'''

    new_after = '''        after = dd[(dd["date"] > trade_date) & (dd["date"] <= as_of_eff)]
        # 第0天（选股日=涨停日）无“涨停后”区间：明显新高=False，均线支撑按空集成立
        if after.empty:
            if offset != 0:
                continue
            signal_date = None
            has_obvious_new_high = False
            ma_support_held = True
            if REQUIRE_OBVIOUS_NEW_HIGH is True:
                continue
            if REQUIRE_MA_SUPPORT_AFTER is False:
                continue
        else:
            max_allowed = limit_up_price * (1.0 + limit_ratio * 0.5)
            max_high_after = float(after["close"].max())
            signal_date = None
            has_obvious_new_high = max_high_after > max_allowed + nh_tol

            if REQUIRE_OBVIOUS_NEW_HIGH is not None:
                if REQUIRE_OBVIOUS_NEW_HIGH:
                    if not has_obvious_new_high:
                        continue
                    nh = after.sort_values("date")
                    nh = nh[nh["close"] > max_allowed + nh_tol]
                    if nh.empty:
                        continue
                    signal_date = nh.iloc[0]["date"]
                elif has_obvious_new_high:
                    continue

            after_ma = after.copy()
            after_ma["MA_MIN_5_10"] = after_ma[["MA5", "MA10"]].min(axis=1, skipna=True)
            after_ma["MA_MIN_20_120"] = after_ma[["MA20", "MA30", "MA60", "MA120"]].min(axis=1, skipna=True)

            bad_short = after_ma["close"] < (after_ma["MA_MIN_5_10"] - 0.01)
            bad_long = after_ma["close"] < (after_ma["MA_MIN_20_120"] - 0.01)
            breaks_ma = bad_short | bad_long
            ma_support_held = not breaks_ma.any()
            if REQUIRE_MA_SUPPORT_AFTER is not None:
                if REQUIRE_MA_SUPPORT_AFTER:
                    if bad_short.any():
                        continue
                    if bad_long.any():
                        continue
                elif not breaks_ma.any():
                    continue'''
    assert old_after in code, "after block not found"

    old_pn = '''    p_day = int(P)
    n_day = int(N)
    if p_day > n_day:
        return False, None, None, None'''
    new_pn = '''    p_day = int(P)
    n_day = int(N)
    if p_day < 0 or n_day < 0 or p_day > n_day:
        return False, None, None, None'''
    assert old_pn in code, "pn check not found"

    code2 = code
    code2 = code2.replace(old_header, new_header, 1)
    code2 = code2.replace(old_offset, new_offset, 1)
    code2 = code2.replace(old_candidates, new_candidates, 1)
    code2 = code2.replace(old_after, new_after, 1)
    code2 = code2.replace(old_pn, new_pn, 1)
    assert code2 != code

    compile(code2, f"<{RULE_NAME}>", "exec")
    rule["code"] = code2
    save_single_sector_rule(rule)
    print("OK updated", RULE_NAME)

    ns: dict = {}
    exec(compile(code2, "<t>", "exec"), ns, ns)
    dates = [date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)]
    assert ns["_post_lu_trading_offset"](dates, dates[1], dates[1]) == 0
    assert ns["_post_lu_trading_offset"](dates, dates[1], dates[2]) == 1
    assert ns["_post_lu_trading_offset"](dates, dates[2], dates[1]) is None
    print("offset checks OK")


if __name__ == "__main__":
    main()
