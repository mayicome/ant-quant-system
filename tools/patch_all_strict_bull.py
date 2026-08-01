# -*- coding: utf-8 -*-
"""给「涨停的第P到N天-全部」加上 REQUIRE_STRICT_BULL=True，共用 core.strict_bull。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(r"d:\蚂蚁量化系统")
sys.path.insert(0, str(ROOT))

from sector_stock_filter import load_sector_rules, save_single_sector_rule  # noqa: E402

NAME = "涨停的第P到N天-全部"

SHARED_GATE = '''
        # 选股日严多头（与严多头/实盘用共用 core.strict_bull）
        asof_rows = dd[dd["date"] == as_of_eff]
        if asof_rows.empty:
            continue
        ar = asof_rows.iloc[-1]
        bull = apply_strict_bull_requirement(
            REQUIRE_STRICT_BULL,
            ar["MA5"],
            ar["MA10"],
            ar["MA20"],
            ar["MA30"],
            ar["MA60"],
            ar["MA120"],
        )
        if bull is None:
            continue
        passed_bull, is_strict_bull = bull
        if not passed_bull:
            continue
'''


def main() -> None:
    rule = next(
        (dict(r) for r in load_sector_rules() if r.get("name") == NAME),
        None,
    )
    if rule is None:
        raise SystemExit(f"rule not found: {NAME}")
    code = str(rule["code"])
    if "apply_strict_bull_requirement" in code and "REQUIRE_STRICT_BULL" in code:
        print("already patched")
        return

    old_param = (
        "REQUIRE_MA_SUPPORT_AFTER = None  # True=涨停后不破均线；"
        "False=须至少一日跌破均线；None=忽略此条件\n"
    )
    new_param = (
        old_param
        + "REQUIRE_STRICT_BULL = None  # True=选股日严多头"
        "(MA5>MA10且MA5>max(MA20,30,60,120))；False=须非严多头；None=忽略\n"
    )
    if old_param not in code:
        raise SystemExit("param line not found")
    code = code.replace(old_param, new_param, 1)

    old_diag = '''def _collect_limit_up_condition_diag(
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
    return {k: _condition_diag_true_false(v) for k, v in diag.items()}'''

    new_diag = '''def _collect_limit_up_condition_diag(
    *,
    has_lower_shadow,
    prev_is_lu,
    boll_break,
    has_old_high,
    has_obvious_new_high,
    ma_support_held,
    has_prior_lu_in_l=None,
    is_strict_bull=None,
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
    if is_strict_bull is not None:
        diag["REQUIRE_STRICT_BULL"] = is_strict_bull
    return {k: _condition_diag_true_false(v) for k, v in diag.items()}'''

    if old_diag not in code:
        raise SystemExit("diag fn not found")
    code = code.replace(old_diag, new_diag, 1)

    old_tail = '''        diag = _collect_limit_up_condition_diag(
            has_lower_shadow=has_lower_shadow,
            prev_is_lu=prev_is_lu,
            boll_break=boll_break,
            has_old_high=has_old_high,
            has_obvious_new_high=has_obvious_new_high,
            ma_support_held=ma_support_held,
            has_prior_lu_in_l=has_prior_lu_in_l,
        )
        return True, trade_date, signal_date, diag'''

    new_tail = (
        SHARED_GATE
        + '''
        diag = _collect_limit_up_condition_diag(
            has_lower_shadow=has_lower_shadow,
            prev_is_lu=prev_is_lu,
            boll_break=boll_break,
            has_old_high=has_old_high,
            has_obvious_new_high=has_obvious_new_high,
            ma_support_held=ma_support_held,
            has_prior_lu_in_l=has_prior_lu_in_l,
            is_strict_bull=is_strict_bull,
        )
        return True, trade_date, signal_date, diag'''
    )
    if old_tail not in code:
        raise SystemExit("diag call not found")
    code = code.replace(old_tail, new_tail, 1)

    compile(code, f"<{NAME}>", "exec")
    rule["code"] = code
    save_single_sector_rule(rule)

    # verify
    from core.strict_bull import apply_strict_bull_requirement
    from datetime import date, timedelta

    ns = {
        "__builtins__": __builtins__,
        "date": date,
        "timedelta": timedelta,
        "apply_strict_bull_requirement": apply_strict_bull_requirement,
    }
    exec(compile(code, NAME, "exec"), ns, ns)
    assert callable(ns["select"])
    assert "REQUIRE_STRICT_BULL = None" in code
    print("OK", NAME)


if __name__ == "__main__":
    main()
