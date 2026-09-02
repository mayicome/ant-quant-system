# -*- coding: utf-8 -*-
"""注册：东财热门对照母集（覆盖空头排列 + 排列并集，且不限均线差 0.5~2%）。

相对「东财热门-无涨停-均线差0.5to2-MA排列并集」：
  - 仍要求：东财热门池 + 近10日无涨停 + MA排列并集（MA5<MA10<MA20 或 MA10<MA20<MA5）
  - 关闭：均线差 ∈[0.5%,2%] 硬过滤（仍计算并输出「均线差占比」）
  - 额外输出对照列，便于一次选股后切片对比两规则：
      条件_均线差0.5to2 / 条件_MA空头排列 / 条件_MA排列并集
      命中_空头规则 / 命中_并集规则
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sector_stock_filter import load_sector_rules, save_single_sector_rule  # noqa: E402

NAME = "东财热门-无涨停-MA排列并集-含均线差对照"
SRC_NAME = "东财热门-无涨停-均线差0.5to2-MA排列并集"

_TAG_HELPER = '''
def _ma_align_flags(ma5, ma10, ma20, gap):
    bear = False
    union_alt = False
    if ma5 is not None and ma10 is not None and ma20 is not None:
        bear = float(ma5) < float(ma10) < float(ma20)
        union_alt = float(ma10) < float(ma20) < float(ma5)
    union = bool(bear or union_alt)
    gap_ok = False
    if gap is not None:
        try:
            g = float(gap)
            gap_ok = float(MA_GAP_LO) <= g <= float(MA_GAP_HI)
        except (TypeError, ValueError):
            gap_ok = False
    return {
        "条件_均线差0.5to2": "是" if gap_ok else "否",
        "条件_MA空头排列": "是" if bear else "否",
        "条件_MA排列并集": "是" if union else "否",
        "命中_空头规则": "是" if (bear and gap_ok) else "否",
        "命中_并集规则": "是" if (union and gap_ok) else "否",
    }
'''


def _inject_compare_tags(code: str) -> str:
    """在两处 extra = { 前插入对照标记，并并入 extra。"""
    if "_ma_align_flags" not in code:
        # 插在第一个 def _elig_hi_for_kind 之前
        anchor = "\ndef _elig_hi_for_kind("
        if anchor not in code:
            raise RuntimeError("找不到 _elig_hi_for_kind 插入点")
        code = code.replace(anchor, "\n" + _TAG_HELPER + anchor, 1)

    needle = "    extra = {\n        \"热门模式\": HOT_MODE,"
    if code.count(needle) < 2:
        raise RuntimeError(f"extra 块数量异常: {code.count(needle)}")
    inject = (
        "    _cmp = _ma_align_flags(ma5, ma10, ma20, gap)\n"
        "    extra = {\n"
        "        \"热门模式\": HOT_MODE,"
    )
    code = code.replace(needle, inject)
    # 在 return True, extra 前把 _cmp 并入（两处）
    old_ret = "    return True, extra"
    new_ret = "    extra.update(_cmp)\n    return True, extra"
    if code.count(old_ret) < 2:
        raise RuntimeError(f"return True 数量异常: {code.count(old_ret)}")
    code = code.replace(old_ret, new_ret)
    return code


def _build_code_from_union() -> str:
    rules = {str(r.get("name") or ""): r for r in load_sector_rules()}
    src = rules.get(SRC_NAME)
    if not src or not str(src.get("code") or "").strip():
        # 回退读文件
        hits = list((ROOT / "data" / "sector_rules").glob("*MA排列并集*.json"))
        if not hits:
            raise FileNotFoundError(f"找不到源规则: {SRC_NAME}")
        src = json.loads(hits[0].read_text(encoding="utf-8"))
    code = str(src.get("code") or "")
    if "REQUIRE_MA_LT_ALIGN" not in code:
        raise RuntimeError("源规则代码异常")

    code = code.replace(
        "# 个股过滤：|MA5-MA10|/min∈[0.5%,2.0%]；近10日无涨停；MA5<MA10<MA20 或 MA10<MA20<MA5",
        "# 个股过滤：近10日无涨停；MA5<MA10<MA20 或 MA10<MA20<MA5；"
        "不硬卡均线差（仍输出均线差占比与命中_*对照列）",
        1,
    )
    code = code.replace("REQUIRE_MA_GAP = True", "REQUIRE_MA_GAP = False", 1)
    code = code.replace(
        'HOT_MODE = "today_elig_sec1_30_con1_40_rs_1_50_frac1of2_ma_union"',
        'HOT_MODE = "today_elig_sec1_50_con1_50_rs_1_50_frac1of2_ma_union_no_gap"',
        1,
    )
    # 若源 HOT_MODE 无 ma_union 后缀
    if "ma_union_no_gap" not in code:
        code = code.replace(
            'HOT_MODE = "today_elig_sec1_50_con1_50_rs_1_50_frac1of2_ma_union"',
            'HOT_MODE = "today_elig_sec1_50_con1_50_rs_1_50_frac1of2_ma_union_no_gap"',
            1,
        )
        code = code.replace(
            'HOT_MODE = "today_elig_sec1_30_con1_40_rs_1_50_frac1of2_ma_union"',
            'HOT_MODE = "today_elig_sec1_50_con1_50_rs_1_50_frac1of2_ma_union_no_gap"',
            1,
        )
        code = code.replace(
            'HOT_MODE = "today_elig_sec1_30_con1_40_rs_1_50_frac1of2"',
            'HOT_MODE = "today_elig_sec1_50_con1_50_rs_1_50_frac1of2_ma_union_no_gap"',
            1,
        )
    code = _inject_compare_tags(code)
    compile(code, f"<{NAME}>", "exec")
    assert "REQUIRE_MA_GAP = False" in code
    assert "REQUIRE_MA_LT_ALIGN = True" in code
    assert "REQUIRE_NO_RECENT_LU = True" in code
    assert "命中_空头规则" in code
    assert "命中_并集规则" in code
    return code


def main() -> None:
    code = _build_code_from_union()
    existing = {str(r.get("name") or ""): dict(r) for r in load_sector_rules()}
    if NAME in existing:
        rule = existing[NAME]
        rule["code"] = code
        rule["enabled"] = bool(rule.get("enabled", False))
    else:
        rule = {
            "id": str(uuid.uuid4()),
            "name": NAME,
            "enabled": False,
            "code": code,
        }
    save_single_sector_rule(rule)
    names = {str(r.get("name")) for r in load_sector_rules()}
    assert NAME in names
    print(f"OK: {NAME}")
    print("  覆盖: MA空头排列 ⊂ MA排列并集；均线差不硬过滤")
    print("  对照列: 条件_均线差0.5to2 / 条件_MA空头排列 / 命中_空头规则 / 命中_并集规则")
    print("  已写入 data/sector_rules/（默认 enabled=False，需在选股页勾选）")


if __name__ == "__main__":
    main()
