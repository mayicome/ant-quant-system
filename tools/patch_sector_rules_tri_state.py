#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""为板块选股规则 JSON 增加 REQUIRE_* 三态支持（True/False/None）。"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = ROOT / "data" / "sector_rules"

TRI_STATE_HELPER = '''
def _apply_bool_tri_state(flag, positive_ok):
    """True=须满足 positive_ok；False=须不满足；None=忽略此条件。"""
    if flag is None:
        return True
    if flag:
        return positive_ok
    return not positive_ok
'''.strip()

COMMENT_REPLACEMENTS = [
    (
        r"(REQUIRE_PRIOR_LU_IN_L = [^\n]+)  # True=L日内至少一个涨停；False=L日内全无涨停",
        r"\1  # True=L日内至少一个涨停；False=L日内全无涨停；None=忽略此条件",
    ),
    (
        r"(REQUIRE_OLD_HIGH = [^\n]+)  # True=须有旧高\(模式二\)；False=须无旧高\(涨停后N日\)",
        r"\1  # True=须有旧高(模式二)；False=须无旧高(涨停后N日)；None=忽略此条件",
    ),
    (
        r"REJECT_PRIOR_LIMIT_UP = ([^\n]+)  # True=排除二连板第二板",
        r"REJECT_PRIOR_LIMIT_UP = \1  # True=排除二连板第二板；False/None=不限制",
    ),
    (
        r"(REQUIRE_OBVIOUS_NEW_HIGH = [^\n]+)  # True=须明显新高；False=须无明显新高",
        r"\1  # True=须明显新高；False=须无明显新高；None=忽略此条件",
    ),
    (
        r"(REQUIRE_LOWER_SHADOW = [^\n]+)  # True=须下影线\(low<前日高\)；False=须跳空涨停\(low>=前日高\)",
        r"\1  # True=须下影线(low<前日高)；False=须跳空涨停(low>=前日高)；None=忽略此条件",
    ),
    (
        r"(REQUIRE_BOLL_BREAK = [^\n]+)  # True=涨停价须>=布林上轨；False=须低于布林上轨",
        r"\1  # True=涨停价须>=布林上轨；False=须低于布林上轨；None=忽略此条件",
    ),
    (
        r"(REQUIRE_MA_SUPPORT_AFTER = [^\n]+)  # True=涨停后不破均线；False=须至少一日跌破均线",
        r"\1  # True=涨停后不破均线；False=须至少一日跌破均线；None=忽略此条件",
    ),
]

OLD_LOWER = """        has_lower_shadow = float(row["low"]) < prev_high
        if REQUIRE_LOWER_SHADOW:
            if not has_lower_shadow:
                continue
        else:
            if has_lower_shadow:
                continue"""

NEW_LOWER = """        has_lower_shadow = float(row["low"]) < prev_high
        if not _apply_bool_tri_state(REQUIRE_LOWER_SHADOW, has_lower_shadow):
            continue"""

OLD_REJECT = """        if REJECT_PRIOR_LIMIT_UP:
            prev_d = prev_row["date"]
            prev_is_lu, _ = _is_limit_up_on_day(dd, prev_d, stock_code, stock_name)
            if prev_is_lu:
                continue"""

NEW_REJECT = """        if REJECT_PRIOR_LIMIT_UP is True:
            prev_d = prev_row["date"]
            prev_is_lu, _ = _is_limit_up_on_day(dd, prev_d, stock_code, stock_name)
            if prev_is_lu:
                continue"""

OLD_BOLL = """        boll_break = limit_up_price >= boll_upper - 0.01
        if REQUIRE_BOLL_BREAK:
            if not boll_break:
                continue
        else:
            if boll_break:
                continue"""

NEW_BOLL = """        boll_break = limit_up_price >= boll_upper - 0.01
        if not _apply_bool_tri_state(REQUIRE_BOLL_BREAK, boll_break):
            continue"""

OLD_OLD_HIGH = """        before = dd[dd["date"] < trade_date]
        if before.empty:
            if REQUIRE_OLD_HIGH:
                continue
        else:
            prev_dates = sorted(before["date"].unique().tolist())
            win_dates = prev_dates[-m:] if len(prev_dates) > m else prev_dates
            win = before[before["date"].isin(win_dates)]
            max_close_before = float(win["close"].max())
            has_old_high = max_close_before >= limit_up_price - 0.01
            if REQUIRE_OLD_HIGH:
                if not has_old_high:
                    continue
            else:
                if has_old_high:
                    continue"""

NEW_OLD_HIGH = """        before = dd[dd["date"] < trade_date]
        if REQUIRE_OLD_HIGH is not None:
            if before.empty:
                if REQUIRE_OLD_HIGH:
                    continue
            else:
                prev_dates = sorted(before["date"].unique().tolist())
                win_dates = prev_dates[-m:] if len(prev_dates) > m else prev_dates
                win = before[before["date"].isin(win_dates)]
                max_close_before = float(win["close"].max())
                has_old_high = max_close_before >= limit_up_price - 0.01
                if REQUIRE_OLD_HIGH:
                    if not has_old_high:
                        continue
                else:
                    if has_old_high:
                        continue"""

OLD_PRIOR_L = """        l_win = int(L)
        if l_win > 0:"""

NEW_PRIOR_L = """        l_win = int(L)
        if l_win > 0 and REQUIRE_PRIOR_LU_IN_L is not None:"""

OLD_PRIOR_L_INNER = """            if REQUIRE_PRIOR_LU_IN_L:
                if not has_prior_lu_in_l:
                    continue
            else:
                if has_prior_lu_in_l:
                    continue"""

NEW_PRIOR_L_INNER = """            if REQUIRE_PRIOR_LU_IN_L:
                if not has_prior_lu_in_l:
                    continue
            else:
                if has_prior_lu_in_l:
                    continue"""

OLD_NH = """        if REQUIRE_OBVIOUS_NEW_HIGH:
            if max_high_after <= max_allowed + nh_tol:
                continue
            nh = after.sort_values("date")
            nh = nh[nh["close"] > max_allowed + nh_tol]
            if nh.empty:
                continue
            signal_date = nh.iloc[0]["date"]
        else:
            if max_high_after > max_allowed + nh_tol:
                continue"""

NEW_NH = """        if REQUIRE_OBVIOUS_NEW_HIGH is not None:
            if REQUIRE_OBVIOUS_NEW_HIGH:
                if max_high_after <= max_allowed + nh_tol:
                    continue
                nh = after.sort_values("date")
                nh = nh[nh["close"] > max_allowed + nh_tol]
                if nh.empty:
                    continue
                signal_date = nh.iloc[0]["date"]
            else:
                if max_high_after > max_allowed + nh_tol:
                    continue"""

OLD_MA = """        if REQUIRE_MA_SUPPORT_AFTER:
            if bad_short.any():
                continue
            if bad_long.any():
                continue
        else:
            if not breaks_ma.any():
                continue"""

NEW_MA = """        if REQUIRE_MA_SUPPORT_AFTER is not None:
            if REQUIRE_MA_SUPPORT_AFTER:
                if bad_short.any():
                    continue
                if bad_long.any():
                    continue
            else:
                if not breaks_ma.any():
                    continue"""


def patch_code(code: str) -> str:
    if "_apply_bool_tri_state" not in code:
        inserted = re.sub(
            r"\n\ndef _dates_up_to\(",
            f"\n\n{TRI_STATE_HELPER}\n\n\ndef _dates_up_to(",
            code,
            count=1,
        )
        if inserted == code:
            raise ValueError("无法定位插入 _apply_bool_tri_state 的位置")
        code = inserted

    for pattern, repl in COMMENT_REPLACEMENTS:
        code = re.sub(pattern, repl, code)

    replacements = [
        (OLD_LOWER, NEW_LOWER),
        (OLD_REJECT, NEW_REJECT),
        (OLD_BOLL, NEW_BOLL),
        (OLD_OLD_HIGH, NEW_OLD_HIGH),
        (OLD_PRIOR_L, NEW_PRIOR_L),
        (OLD_NH, NEW_NH),
        (OLD_MA, NEW_MA),
    ]
    for old, new in replacements:
        if old in code:
            code = code.replace(old, new)

    return code


def main() -> None:
    for path in sorted(RULES_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        code = data.get("code", "")
        if not code or "REQUIRE_LOWER_SHADOW" not in code:
            continue
        new_code = patch_code(code)
        if new_code != code:
            data["code"] = new_code
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"patched: {path.name}")
        else:
            print(f"skip (already patched): {path.name}")


if __name__ == "__main__":
    main()
