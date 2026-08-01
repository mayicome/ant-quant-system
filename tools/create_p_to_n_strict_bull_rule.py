# -*- coding: utf-8 -*-
"""Clone 涨停的第P到N天-全部 → 涨停的第P到N天-严多头."""
from __future__ import annotations

import json
import math
import sys
import uuid
from pathlib import Path

ROOT = Path(r"d:\蚂蚁量化系统")
sys.path.insert(0, str(ROOT))

from sector_stock_filter import load_sector_rules, save_single_sector_rule  # noqa: E402

RULES_DIR = ROOT / "data" / "sector_rules"


def main() -> None:
    src = next(
        p
        for p in RULES_DIR.glob("*.json")
        if json.loads(p.read_text(encoding="utf-8")).get("name") == "涨停的第P到N天-全部"
    )
    cfg = json.loads(src.read_text(encoding="utf-8"))
    code = cfg["code"]

    old_param = (
        "REQUIRE_MA_SUPPORT_AFTER = None  # True=涨停后不破均线；"
        "False=须至少一日跌破均线；None=忽略此条件"
    )
    new_param = (
        old_param
        + "\n"
        + "REQUIRE_STRICT_BULL = True  # True=选股日严多头"
        + "(MA5>MA10且MA5>max(MA20,30,60,120))；False=须非严多头；None=忽略"
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
        raise SystemExit("diag function not found")
    code = code.replace(old_diag, new_diag, 1)

    old_tail = '''        if REQUIRE_MA_SUPPORT_AFTER is not None:
            if REQUIRE_MA_SUPPORT_AFTER:
                if bad_short.any():
                    continue
                if bad_long.any():
                    continue
            elif not breaks_ma.any():
                continue

        diag = _collect_limit_up_condition_diag(
            has_lower_shadow=has_lower_shadow,
            prev_is_lu=prev_is_lu,
            boll_break=boll_break,
            has_old_high=has_old_high,
            has_obvious_new_high=has_obvious_new_high,
            ma_support_held=ma_support_held,
            has_prior_lu_in_l=has_prior_lu_in_l,
        )
        return True, trade_date, signal_date, diag'''

    new_tail = '''        if REQUIRE_MA_SUPPORT_AFTER is not None:
            if REQUIRE_MA_SUPPORT_AFTER:
                if bad_short.any():
                    continue
                if bad_long.any():
                    continue
            elif not breaks_ma.any():
                continue

        # 选股日严多头：MA5>MA10 且 MA5>max(MA20,MA30,MA60,MA120)
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
        if any(math.isnan(x) for x in (ma5v, ma10v, ma20v, ma30v, ma60v, ma120v)):
            if REQUIRE_STRICT_BULL is True:
                continue
            is_strict_bull = False
        else:
            longer = max(ma20v, ma30v, ma60v, ma120v)
            is_strict_bull = (ma5v > ma10v) and (ma5v > longer)
        if not _apply_bool_tri_state(REQUIRE_STRICT_BULL, is_strict_bull):
            continue

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

    # math used inside strict-bull check; keep imports after params by inlining
    new_tail = new_tail.replace(
        "if any(math.isnan(x) for x in (ma5v, ma10v, ma20v, ma30v, ma60v, ma120v)):",
        "import math as _math\n"
        "        if any(_math.isnan(x) for x in (ma5v, ma10v, ma20v, ma30v, ma60v, ma120v)):",
    )

    if old_tail not in code:
        raise SystemExit("tail block not found")
    code = code.replace(old_tail, new_tail, 1)

    compile(code, "<涨停的第P到N天-严多头>", "exec")

    # avoid duplicate name
    existing = [r for r in load_sector_rules() if r.get("name") == "涨停的第P到N天-严多头"]
    if existing:
        rid = str(existing[0]["id"])
        print("updating existing", rid)
    else:
        rid = str(uuid.uuid4())
        print("creating", rid)

    rule = {
        "id": rid,
        "name": "涨停的第P到N天-严多头",
        "enabled": True,
        "code": code,
    }
    save_single_sector_rule(rule)

    names = [str(r.get("name")) for r in load_sector_rules()]
    assert "涨停的第P到N天-严多头" in names
    assert "涨停的第P到N天-全部" in names
    print("OK:", [n for n in names if "P到N" in n])


if __name__ == "__main__":
    main()
