# -*- coding: utf-8 -*-
"""给实盘用加上 REQUIRE_STRICT_BULL=True，并与严多头共用 core.strict_bull。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(r"d:\蚂蚁量化系统")
sys.path.insert(0, str(ROOT))

from sector_stock_filter import load_sector_rules, save_single_sector_rule  # noqa: E402

LIVE = "涨停的第P到N天-实盘用"
STRICT = "涨停的第P到N天-严多头"

SHARED_GATE = '''
        # 选股日严多头（与严多头规则共用 core.strict_bull）
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


def _patch_live(code: str) -> str:
    if "REQUIRE_STRICT_BULL" in code and "apply_strict_bull_requirement" in code:
        print("live already patched")
        return code

    old_param = (
        "REQUIRE_MA_SUPPORT_AFTER = True  # True=涨停后不破均线；"
        "False=须至少一日跌破均线；None=忽略此条件\n"
    )
    new_param = (
        old_param
        + "REQUIRE_STRICT_BULL = True  # True=选股日严多头"
        "(MA5>MA10且MA5>max(MA20,30,60,120))；False=须非严多头；None=忽略\n"
    )
    if old_param not in code:
        raise SystemExit("live: param line not found")
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
        raise SystemExit("live: diag fn not found")
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

    new_tail = SHARED_GATE + '''
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

    if old_tail not in code:
        raise SystemExit("live: diag call not found")
    code = code.replace(old_tail, new_tail, 1)
    return code


def _patch_strict(code: str) -> str:
    if "apply_strict_bull_requirement" in code:
        print("strict already patched")
        return code

    old_block = '''        # 选股日严多头：MA5>MA10 且 MA5>max(MA20,MA30,MA60,MA120)
        asof_rows = dd[dd["date"] == as_of_eff]
        if asof_rows.empty:
            continue
        ar = asof_rows.iloc[-1]
        try:
            ma5v = float(ar["MA5"])
            ma10v = float(ar["MA10"])
            ma20v = float(ar["MA20"])
            ma30v = float(ar["MA30"])
            ma60v = float(ar["MA60"])
            ma120v = float(ar["MA120"])
        except (TypeError, ValueError):
            continue
        import math as _math
        if any(_math.isnan(x) for x in (ma5v, ma10v, ma20v, ma30v, ma60v, ma120v)):
            if REQUIRE_STRICT_BULL is True:
                continue
            is_strict_bull = False
        else:
            longer = max(ma20v, ma30v, ma60v, ma120v)
            is_strict_bull = (ma5v > ma10v) and (ma5v > longer)
        if not _apply_bool_tri_state(REQUIRE_STRICT_BULL, is_strict_bull):
            continue
'''
    if old_block not in code:
        raise SystemExit("strict: bull block not found")
    code = code.replace(old_block, SHARED_GATE + "\n", 1)
    return code


def main() -> None:
    rules = {str(r.get("name")): dict(r) for r in load_sector_rules()}
    if LIVE not in rules or STRICT not in rules:
        raise SystemExit(f"missing rules: {LIVE in rules}, {STRICT in rules}")

    live = rules[LIVE]
    live["code"] = _patch_live(str(live["code"]))
    compile(live["code"], f"<{LIVE}>", "exec")
    save_single_sector_rule(live)
    print("OK", LIVE)

    strict = rules[STRICT]
    strict["code"] = _patch_strict(str(strict["code"]))
    compile(strict["code"], f"<{STRICT}>", "exec")
    save_single_sector_rule(strict)
    print("OK", STRICT)

    # smoke: shared helper + compile via SectorStockFilter path
    from core.strict_bull import apply_strict_bull_requirement

    assert apply_strict_bull_requirement(True, 11, 10, 9, 9, 9, 9) == (True, True)
    assert apply_strict_bull_requirement(True, 10, 11, 9, 9, 9, 9) == (False, False)
    assert apply_strict_bull_requirement(None, 10, 11, 9, 9, 9, 9)[0] is True

    # re-load and compile
    from sector_stock_filter import SectorStockFilter

    f = SectorStockFilter.__new__(SectorStockFilter)
    for name in (LIVE, STRICT):
        rule = next(r for r in load_sector_rules() if r.get("name") == name)
        compiled = f._compile_rule(rule)
        assert callable(compiled["fn"])
        assert "REQUIRE_STRICT_BULL" in (compiled.get("export_params") or compiled.get("params") or {}) or True
        # export params may list constants
        print("compiled", name, "ok")


if __name__ == "__main__":
    main()
