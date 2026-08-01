#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""选股规则：命中结果 extra 写入各条件实际 True/False（非规则开关配置）。"""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULES_DIR = ROOT / "data" / "sector_rules"

CONDITION_DIAG_HELPER = '''
def _condition_diag_true_false(flag):
    return "True" if flag else "False"


def _collect_limit_up_condition_diag(
    *,
    has_lower_shadow,
    prev_is_lu,
    boll_break,
    has_old_high,
    has_obvious_new_high,
    ma_support_held,
    has_prior_lu_in_l=None,
):
    """命中时写入 extra：各条件对该股的实际 True/False，与规则开关配置无关。"""
    diag = {
        "REQUIRE_LOWER_SHADOW": has_lower_shadow,
        "REJECT_PRIOR_LIMIT_UP": prev_is_lu,
        "REQUIRE_BOLL_BREAK": boll_break,
        "REQUIRE_OLD_HIGH": has_old_high,
        "REQUIRE_OBVIOUS_NEW_HIGH": has_obvious_new_high,
        "REQUIRE_MA_SUPPORT_AFTER": ma_support_held,
    }
    if has_prior_lu_in_l is not None:
        diag["REQUIRE_PRIOR_LU_IN_L"] = has_prior_lu_in_l
    return {k: _condition_diag_true_false(v) for k, v in diag.items()}
'''.strip()

OLD_SELECT = """def select(stock_code, stock_name, sectors, daily_data, as_of_date, ctx):
    ok, limit_up_date, signal_date = _screen_limit_up(stock_code, stock_name, daily_data, as_of_date)
    extra = {}
    if limit_up_date is not None:
        extra["涨停日期"] = limit_up_date.strftime("%Y-%m-%d")
    if signal_date is not None:
        extra["信号日期"] = signal_date.strftime("%Y-%m-%d")
    return bool(ok), extra"""

NEW_SELECT = """def select(stock_code, stock_name, sectors, daily_data, as_of_date, ctx):
    ok, limit_up_date, signal_date, diag = _screen_limit_up(stock_code, stock_name, daily_data, as_of_date)
    extra = {}
    if limit_up_date is not None:
        extra["涨停日期"] = limit_up_date.strftime("%Y-%m-%d")
    if signal_date is not None:
        extra["信号日期"] = signal_date.strftime("%Y-%m-%d")
    if isinstance(diag, dict):
        extra.update(diag)
    return bool(ok), extra"""

OLD_SUCCESS_RETURN = """        return True, trade_date, signal_date

    return False, None, None"""

NEW_SUCCESS_RETURN = """        diag = _collect_limit_up_condition_diag(
            has_lower_shadow=has_lower_shadow,
            prev_is_lu=prev_is_lu,
            boll_break=boll_break,
            has_old_high=has_old_high,
            has_obvious_new_high=has_obvious_new_high,
            ma_support_held=ma_support_held,
            has_prior_lu_in_l=has_prior_lu_in_l,
        )
        return True, trade_date, signal_date, diag

    return False, None, None, None"""

OLD_LOOP_TAIL = """        has_lower_shadow = float(row["low"]) < prev_high
        if not _apply_bool_tri_state(REQUIRE_LOWER_SHADOW, has_lower_shadow):
            continue

        if REJECT_PRIOR_LIMIT_UP is True:
            prev_d = prev_row["date"]
            prev_is_lu, _ = _is_limit_up_on_day(dd, prev_d, stock_code, stock_name)
            if prev_is_lu:
                continue

        limit_day = dd[dd["date"] == trade_date]
        if limit_day.empty:
            continue
        boll_upper = float(limit_day["BOLL_UPPER"].iloc[0])
        boll_break = limit_up_price >= boll_upper - 0.01
        if not _apply_bool_tri_state(REQUIRE_BOLL_BREAK, boll_break):
            continue

        before = dd[dd["date"] < trade_date]
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

NEW_LOOP_HEAD = """        has_lower_shadow = float(row["low"]) < prev_high
        if not _apply_bool_tri_state(REQUIRE_LOWER_SHADOW, has_lower_shadow):
            continue

        prev_d = prev_row["date"]
        prev_is_lu, _ = _is_limit_up_on_day(dd, prev_d, stock_code, stock_name)
        if REJECT_PRIOR_LIMIT_UP is True and prev_is_lu:
            continue

        limit_day = dd[dd["date"] == trade_date]
        if limit_day.empty:
            continue
        boll_upper = float(limit_day["BOLL_UPPER"].iloc[0])
        boll_break = limit_up_price >= boll_upper - 0.01
        if not _apply_bool_tri_state(REQUIRE_BOLL_BREAK, boll_break):
            continue

        before = dd[dd["date"] < trade_date]
        has_old_high = False
        if not before.empty:
            prev_dates = sorted(before["date"].unique().tolist())
            win_dates = prev_dates[-m:] if len(prev_dates) > m else prev_dates
            win = before[before["date"].isin(win_dates)]
            max_close_before = float(win["close"].max())
            has_old_high = max_close_before >= limit_up_price - 0.01
        if REQUIRE_OLD_HIGH is not None:
            if before.empty:
                if REQUIRE_OLD_HIGH:
                    continue
            elif REQUIRE_OLD_HIGH:
                if not has_old_high:
                    continue
            else:
                if has_old_high:
                    continue"""

OLD_PRIOR_L_PTON = """        l_win = int(L)
        if l_win > 0 and REQUIRE_PRIOR_LU_IN_L is not None:"""

NEW_PRIOR_L_PTON = """        has_prior_lu_in_l = None
        l_win = int(L)
        if l_win > 0:"""

OLD_PRIOR_L_INNER = """            if REQUIRE_PRIOR_LU_IN_L:
                if not has_prior_lu_in_l:
                    continue
            else:
                if has_prior_lu_in_l:
                    continue

        after = dd[(dd["date"] > trade_date) & (dd["date"] <= as_of_eff)]"""

NEW_PRIOR_L_INNER = """            if REQUIRE_PRIOR_LU_IN_L is not None:
                if REQUIRE_PRIOR_LU_IN_L:
                    if not has_prior_lu_in_l:
                        continue
                else:
                    if has_prior_lu_in_l:
                        continue

        after = dd[(dd["date"] > trade_date) & (dd["date"] <= as_of_eff)]"""

OLD_PRIOR_L_MODE12 = """        l_win = int(L)
        if l_win > 0 and REQUIRE_PRIOR_LU_IN_L is not None:
            prev_dates_all = sorted(before["date"].unique().tolist())
            if not prev_dates_all:
                continue
            lb_dates = (
                prev_dates_all[-l_win:]
                if len(prev_dates_all) >= l_win
                else prev_dates_all
            )
            has_prior_lu_in_l = False
            for d_chk in lb_dates:
                if _is_limit_up_on_day(dd, d_chk, stock_code, stock_name)[0]:
                    has_prior_lu_in_l = True
                    break
            if REQUIRE_PRIOR_LU_IN_L:
                if not has_prior_lu_in_l:
                    continue
            else:
                if has_prior_lu_in_l:
                    continue
        after = dd[dd["date"] > trade_date]"""

NEW_PRIOR_L_MODE12 = """        has_prior_lu_in_l = None
        l_win = int(L)
        if l_win > 0:
            prev_dates_all = sorted(before["date"].unique().tolist())
            if not prev_dates_all:
                continue
            lb_dates = (
                prev_dates_all[-l_win:]
                if len(prev_dates_all) >= l_win
                else prev_dates_all
            )
            has_prior_lu_in_l = False
            for d_chk in lb_dates:
                if _is_limit_up_on_day(dd, d_chk, stock_code, stock_name)[0]:
                    has_prior_lu_in_l = True
                    break
            if REQUIRE_PRIOR_LU_IN_L is not None:
                if REQUIRE_PRIOR_LU_IN_L:
                    if not has_prior_lu_in_l:
                        continue
                else:
                    if has_prior_lu_in_l:
                        continue
        after = dd[dd["date"] > trade_date]"""

OLD_AFTER_BLOCK = """        max_allowed = limit_up_price * (1.0 + limit_ratio * 0.5)
        max_high_after = float(after["close"].max())
        signal_date = None

        if REQUIRE_OBVIOUS_NEW_HIGH is not None:
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
                    continue

        after_ma = after.copy()
        after_ma["MA_MIN_5_10"] = after_ma[["MA5", "MA10"]].min(axis=1, skipna=True)
        after_ma["MA_MIN_20_120"] = after_ma[["MA20", "MA30", "MA60", "MA120"]].min(axis=1, skipna=True)

        bad_short = after_ma["close"] < (after_ma["MA_MIN_5_10"] - 0.01)
        bad_long = after_ma["close"] < (after_ma["MA_MIN_20_120"] - 0.01)
        breaks_ma = bad_short | bad_long
        if REQUIRE_MA_SUPPORT_AFTER is not None:
            if REQUIRE_MA_SUPPORT_AFTER:
                if bad_short.any():
                    continue
                if bad_long.any():
                    continue
            else:
                if not breaks_ma.any():
                    continue"""

NEW_AFTER_BLOCK = """        max_allowed = limit_up_price * (1.0 + limit_ratio * 0.5)
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
                continue"""


def patch_code(code: str) -> str:
    if "_collect_limit_up_condition_diag" in code:
        return code

    if "_apply_bool_tri_state" in code:
        code = code.replace(
            "def _apply_bool_tri_state(flag, positive_ok):",
            f"{CONDITION_DIAG_HELPER}\n\n\ndef _apply_bool_tri_state(flag, positive_ok):",
            1,
        )
    else:
        code = re.sub(
            r"\n\ndef _dates_up_to\(",
            f"\n\n{CONDITION_DIAG_HELPER}\n\n\ndef _dates_up_to(",
            code,
            count=1,
        )

    code = re.sub(
        r"return False, None, None(?:, None)?\n",
        "return False, None, None, None\n",
        code,
    )
    code = code.replace(OLD_LOOP_TAIL, NEW_LOOP_HEAD)
    if OLD_PRIOR_L_PTON in code:
        code = code.replace(OLD_PRIOR_L_PTON, NEW_PRIOR_L_PTON)
        code = code.replace(OLD_PRIOR_L_INNER, NEW_PRIOR_L_INNER)
    elif OLD_PRIOR_L_MODE12 in code:
        code = code.replace(OLD_PRIOR_L_MODE12, NEW_PRIOR_L_MODE12)
    code = code.replace(OLD_AFTER_BLOCK, NEW_AFTER_BLOCK)
    if "has_prior_lu_in_l = None" not in code:
        code = code.replace(
            '\n        after = dd[dd["date"] > trade_date]',
            '\n        has_prior_lu_in_l = None\n        after = dd[dd["date"] > trade_date]',
            1,
        )
    code = code.replace(OLD_SUCCESS_RETURN, NEW_SUCCESS_RETURN)
    code = code.replace(OLD_SELECT, NEW_SELECT)
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
            print(f"skip: {path.name}")


if __name__ == "__main__":
    main()
