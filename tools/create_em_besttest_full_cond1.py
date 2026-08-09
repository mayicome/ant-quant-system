# -*- coding: utf-8 -*-
"""注册：东财热门 besttest 全量 + 条件一 + 条件三。

架构说明（Cond2 不能隔夜选股）：
  - 条件一：写入本选股规则（收盘后可知）— 近10日无涨停 + 均线差 0.5%~2%
  - 条件三：写入本选股规则（选股日收盘 MA5<MA10<MA20）
  - 条件二（开盘相对早盘 MA5∈[0%,2%]）：策略生成 09:25（需今开盘）
  - 开盘夹档 + Cond2 + clip(2,4)：策略「开盘MA5/MA10夹档买入-条件二开盘相对MA5_0to2」

顺带确保底座规则「东财热门-besttest全量-无个股过滤」存在（ANY_TAG=False）。
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sector_rule_templates import rule_code_em_today_hot_elig_band  # noqa: E402
from sector_stock_filter import (  # noqa: E402
    delete_sector_rule_files,
    load_sector_rules,
    save_single_sector_rule,
)

NAME_FULL = "东财热门-besttest全量-无个股过滤"
# 旧 Cond1-only 名（迁移时复用 id 后删除残留）
NAME_COND1_OLD = "东财热门-besttest全量-条件一无涨停均线差0.5to2"
NAME_COND13 = (
    "东财热门-besttest全量-条件一条件三-无涨停均线差0.5to2-MA空头排列"
)


def _apply_live_frac_half(code: str) -> str:
    """与现网东财热门一致：组内 RS 前 1/2（模板默认 1/3）。"""
    code = code.replace("RS_TOP_FRAC_DEN = 3", "RS_TOP_FRAC_DEN = 2")
    return (
        code.replace("前1/3", "前1/2")
        .replace("ceil(样本/3)", "ceil(样本/2)")
        .replace("frac1of3", "frac1of2")
    )


def _base_kwargs(**overrides):
    kw = dict(
        elig_lo=1,
        elig_hi=30,
        top_n=50,
        rs_top_k=50,
        rs_lo=1,
        rs_hi=50,
        rs_lookback=10,
        min_members=10,
        ma_gap_lo=0.005,
        ma_gap_hi=0.02,
        min_float_mv_yi=120.0,
        any_tag=False,  # besttest = 只认最热标签
        apply_stock_filters=False,
    )
    kw.update(overrides)
    return kw


def _upsert(name: str, code: str, *, enabled: Optional[bool] = None) -> str:
    compile(code, f"<{name}>", "exec")
    existing = {str(r.get("name") or ""): dict(r) for r in load_sector_rules()}
    if name in existing:
        rule = existing[name]
        rule["code"] = code
        if enabled is not None:
            rule["enabled"] = bool(enabled)
        else:
            rule["enabled"] = bool(rule.get("enabled", True))
    else:
        rule = {
            "id": str(uuid.uuid4()),
            "name": name,
            "enabled": True if enabled is None else bool(enabled),
            "code": code,
        }
    save_single_sector_rule(rule)
    return str(rule["id"])


def _upsert_cond13(code: str) -> str:
    """启用 Cond1+Cond3；若存在旧 Cond1-only 规则则复用其 id 并改名。"""
    compile(code, f"<{NAME_COND13}>", "exec")
    by_name = {str(r.get("name") or ""): dict(r) for r in load_sector_rules()}
    if NAME_COND13 in by_name:
        rule = by_name[NAME_COND13]
    elif NAME_COND1_OLD in by_name:
        rule = by_name[NAME_COND1_OLD]
        rule["name"] = NAME_COND13
    else:
        rule = {
            "id": str(uuid.uuid4()),
            "name": NAME_COND13,
            "enabled": True,
            "code": code,
        }
    rule["code"] = code
    rule["enabled"] = True
    rule["name"] = NAME_COND13
    save_single_sector_rule(rule)
    # 若仍残留旧名同内容以外的文件，按 id 已由 save 清理；再删孤儿旧名
    leftover = [
        r
        for r in load_sector_rules()
        if str(r.get("name") or "") == NAME_COND1_OLD
        and str(r.get("id") or "") != str(rule["id"])
    ]
    for r in leftover:
        delete_sector_rule_files(str(r["id"]))
    return str(rule["id"])


def main() -> None:
    code_full = _apply_live_frac_half(
        rule_code_em_today_hot_elig_band(**_base_kwargs())
    )
    assert "ANY_TAG = False" in code_full
    assert "APPLY_STOCK_FILTERS = False" in code_full
    assert "REQUIRE_MA_GAP = False" in code_full
    assert "REQUIRE_NO_RECENT_LU = False" in code_full
    assert "REQUIRE_MA_LT_ALIGN = False" in code_full
    rid_full = _upsert(NAME_FULL, code_full, enabled=True)

    code_c13 = _apply_live_frac_half(
        rule_code_em_today_hot_elig_band(
            **_base_kwargs(
                require_ma_gap=True,
                require_no_recent_lu=True,
                require_ma_lt_align=True,  # Cond3：选股日 MA5<MA10<MA20
                require_min_float_mv=False,  # 不跟 A 档市值门槛
            )
        )
    )
    assert "ANY_TAG = False" in code_c13
    assert "REQUIRE_MA_GAP = True" in code_c13
    assert "REQUIRE_NO_RECENT_LU = True" in code_c13
    assert "REQUIRE_MA_LT_ALIGN = True" in code_c13
    assert "REQUIRE_MIN_FLOAT_MV = False" in code_c13
    assert "APPLY_STOCK_FILTERS = True" in code_c13  # 子开关开启 → 兼容 True
    assert "MA5<MA10<MA20" in code_c13
    rid_c13 = _upsert_cond13(code_c13)

    names = {str(r.get("name")) for r in load_sector_rules()}
    assert NAME_FULL in names
    assert NAME_COND13 in names
    assert NAME_COND1_OLD not in names

    print(f"OK: {NAME_FULL}")
    print(f"  id={rid_full}  ANY_TAG=False  无个股硬过滤  ELIG[1,30]  RS前1/2")
    print(f"OK: {NAME_COND13}")
    print(f"  id={rid_c13}  ANY_TAG=False")
    print("  Cond1=无涨停+均线差[0.5%,2%]  Cond3=选股日MA5<MA10<MA20")
    print("  Cond2 不在选股：请用策略「开盘MA5/MA10夹档买入-条件二开盘相对MA5_0to2」")
    print("    Cond2 强制开 + clip_equity L=2 U=4（strategy_e6d1b97b）")
    print("    （require_ma5_lt_ma10_lt_ma20 默认关，避免与选股 Cond3 双重过滤）")
    print("  已写入 data/sector_rules/")


if __name__ == "__main__":
    main()
