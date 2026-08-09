# -*- coding: utf-8 -*-
"""从 besttest MA空头规则克隆，并植入选股日分时尾盘过滤 → 写入 data/sector_rules。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "sector_rules" / (
    "东财热门-besttest-无涨停均线差0.5to2-MA空头排列__de4a81d4.json"
)
OUT_DIR = ROOT / "data" / "sector_rules"

TICK_HELPERS = r'''
# ---- 选股日分时因子挂载（data/ticks/{YYYYMMDD}/{code}.parquet；默认只挂列）----
# 先导出分时_* 列看分布，再按需把下面开关改为 True 做淘汰
REQUIRE_TICK_DATA = False
REQUIRE_PM_FLOOR = False
REQUIRE_LATE_FLOOR = False
REQUIRE_CLOSE_NEAR_VWAP = False
REJECT_FRONTLOAD_FADE = False
PM_RET_LO = -0.02
LATE_RET_LO = -0.015
CLOSE_VWAP_LO = -0.01
AM_VOL_FRAC_HI = 0.75


def _apply_tick_tail_filters(stock_code, as_of_date):
    """返回 (ok, skip_reason, factor_dict)。"""
    try:
        from utils.tick_session_factors import (
            compute_session_factors,
            evaluate_tick_filters,
        )
    except Exception as e:
        if REQUIRE_TICK_DATA:
            return False, "分时模块导入失败:%s" % e, {}
        return True, "", {}
    fac = compute_session_factors(stock_code, as_of_date)
    ok, skip, out = evaluate_tick_filters(
        fac,
        require_tick_data=bool(REQUIRE_TICK_DATA),
        pm_ret_lo=float(PM_RET_LO),
        late_ret_lo=float(LATE_RET_LO),
        close_vwap_lo=float(CLOSE_VWAP_LO),
        require_pm_floor=bool(REQUIRE_PM_FLOOR),
        require_late_floor=bool(REQUIRE_LATE_FLOOR),
        require_close_near_vwap=bool(REQUIRE_CLOSE_NEAR_VWAP),
        reject_frontload_fade=bool(REJECT_FRONTLOAD_FADE),
        am_vol_frac_hi=float(AM_VOL_FRAC_HI),
    )
    return ok, skip, out

'''

# Insert after stock filters, before building success extra — hottest path marker
HOT_ANCHOR = '    tag = hit.get("合格榜对应标签", "")'
HOT_INSERT = '''    tick_ok, tick_skip, tick_fac = _apply_tick_tail_filters(stock_code, as_of_date)
    if not tick_ok:
        base = {
            "热门模式": HOT_MODE,
            "合格榜内序位": elig,
            "合格榜对应标签": hit.get("合格榜对应标签", ""),
            "_skip": tick_skip or "分时尾盘过滤未通过",
        }
        base.update(tick_fac or {})
        return False, base

    tag = hit.get("合格榜对应标签", "")'''

ANY_ANCHOR = '    extra = {\n        "热门模式": HOT_MODE,\n        "合格榜内序位": hit.get("合格榜内序位", ""),'
ANY_INSERT = '''    tick_ok, tick_skip, tick_fac = _apply_tick_tail_filters(stock_code, as_of_date)
    if not tick_ok:
        base = {
            "热门模式": HOT_MODE,
            "合格榜内序位": hit.get("合格榜内序位", ""),
            "选出标签": best.get("tag", ""),
            "_skip": tick_skip or "分时尾盘过滤未通过",
        }
        base.update(tick_fac or {})
        return False, base

    extra = {
        "热门模式": HOT_MODE,
        "合格榜内序位": hit.get("合格榜内序位", ""),'''

# Merge tick factors into success extras
EXTRA_CLOSE_MARKERS = [
    ('        "APPLY_STOCK_FILTERS": bool(APPLY_STOCK_FILTERS),\n    }\n    return True, extra',
     '        "APPLY_STOCK_FILTERS": bool(APPLY_STOCK_FILTERS),\n'
     '        "REQUIRE_TICK_DATA": bool(REQUIRE_TICK_DATA),\n'
     '        "PM_RET_LO": float(PM_RET_LO),\n'
     '        "LATE_RET_LO": float(LATE_RET_LO),\n'
     '        "CLOSE_VWAP_LO": float(CLOSE_VWAP_LO),\n'
     '    }\n'
     '    extra.update(tick_fac or {})\n'
     '    return True, extra'),
]


def main() -> None:
    raw = json.loads(BASE.read_text(encoding="utf-8"))
    code = str(raw["code"])
    if "分时_午后收益" in code or "_apply_tick_tail_filters" in code:
        raise SystemExit("base code already looks patched")

    # header comment + helpers after HOT_MODE / N block
    if "HOT_MODE =" not in code:
        raise SystemExit("unexpected base rule")
    # insert helpers after N = 20 block
    needle = "N = 20\n"
    idx = code.find(needle)
    if idx < 0:
        raise SystemExit("cannot find N = 20")
    insert_at = idx + len(needle)
    code = code[:insert_at] + "\n" + TICK_HELPERS + code[insert_at:]

    if HOT_ANCHOR not in code:
        raise SystemExit("HOT_ANCHOR missing")
    code = code.replace(HOT_ANCHOR, HOT_INSERT, 1)

    if ANY_ANCHOR not in code:
        raise SystemExit("ANY_ANCHOR missing")
    code = code.replace(ANY_ANCHOR, ANY_INSERT, 1)

    # both return True, extra paths — replace all occurrences of APPLY_STOCK_FILTERS close
    old, new = EXTRA_CLOSE_MARKERS[0]
    count = code.count(old)
    if count < 2:
        raise SystemExit("expected 2 success returns, found %d" % count)
    code = code.replace(old, new)

    # update header comment
    code = code.replace(
        "# 个股过滤：|MA5-MA10|/min∈[0.5%,2.0%]；近10日无涨停；MA5<MA10<MA20\n",
        "# 个股过滤：|MA5-MA10|/min∈[0.5%,2.0%]；近10日无涨停；MA5<MA10<MA20\n"
        "# 分时因子：选股日 data/ticks 挂列（默认不过滤；开关 REQUIRE_* 后再淘汰）\n",
        1,
    )

    rid = str(uuid.uuid4())
    name = "东财热门-besttest-均线差0.5to2-MA空头-分时因子"
    short = rid.split("-")[0]
    out = {
        "id": rid,
        "name": name,
        "enabled": True,
        "code": code,
    }
    path = OUT_DIR / ("%s__%s.json" % (name, short))
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("wrote", path)
    print("id", rid)
    print("enabled True — 请在选股 GUI 中确认；旧 besttest 规则仍启用时可按需关掉其一")


if __name__ == "__main__":
    main()
