# -*- coding: utf-8 -*-
"""注册头档内组内 RS 三档：1–10 / 11–20 / ≥21（Elig 固定 1–15）。"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path
from typing import Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sector_rule_templates import rule_code_em_today_hot_elig_band  # noqa: E402
from sector_stock_filter import load_sector_rules, save_single_sector_rule  # noqa: E402

# (name, rs_lo, rs_hi)  rs_hi=None → ≥ rs_lo
RULES: Tuple[Tuple[str, int, Optional[int]], ...] = (
    (
        "东财热门-今日头档Elig1to15-RS1to10-夹档-无涨停-MA5ltMA10lt20-流通ge120",
        1,
        10,
    ),
    (
        "东财热门-今日头档Elig1to15-RS11to20-夹档-无涨停-MA5ltMA10lt20-流通ge120",
        11,
        20,
    ),
    (
        "东财热门-今日头档Elig1to15-RSge21-夹档-无涨停-MA5ltMA10lt20-流通ge120",
        21,
        None,
    ),
)


def _upsert(name: str, rs_lo: int, rs_hi: Optional[int]) -> None:
    code = rule_code_em_today_hot_elig_band(
        elig_lo=1,
        elig_hi=15,
        top_n=50,
        rs_top_k=20,
        rs_lo=rs_lo,
        rs_hi=rs_hi,
        rs_lookback=10,
        min_members=30,
        ma_gap_lo=0.005,
        ma_gap_hi=0.02,
        min_float_mv_yi=120.0,
    )
    compile(code, f"<{name}>", "exec")
    existing = {str(r.get("name") or ""): r for r in load_sector_rules()}
    old = existing.get(name)
    rid = str(old.get("id") or uuid.uuid4()) if old else str(uuid.uuid4())
    enabled = bool(old.get("enabled", True)) if old else True
    save_single_sector_rule(
        {
            "id": rid,
            "name": name,
            "enabled": enabled,
            "code": code,
        }
    )
    action = "更新" if old else "新增"
    rs_txt = f">={rs_lo}" if rs_hi is None else f"[{rs_lo},{rs_hi}]"
    print(f"{action}: {name}  id={rid}  elig=[1,15]  RS{rs_txt}")


def main() -> None:
    # 顺带刷新旧「头档 RS20」规则，使其显式过滤合格榜标签内 RS∈[1,20]
    legacy = "东财热门-今日Top50-头档Elig1to15-RS20-夹档-无涨停-MA5ltMA10lt20-流通ge120"
    code_legacy = rule_code_em_today_hot_elig_band(
        elig_lo=1, elig_hi=15, rs_lo=1, rs_hi=20, min_float_mv_yi=120.0
    )
    compile(code_legacy, f"<{legacy}>", "exec")
    existing = {str(r.get("name") or ""): r for r in load_sector_rules()}
    old = existing.get(legacy)
    if old:
        save_single_sector_rule(
            {
                "id": str(old.get("id") or uuid.uuid4()),
                "name": legacy,
                "enabled": bool(old.get("enabled", True)),
                "code": code_legacy,
            }
        )
        print(f"刷新旧头档规则 RS 显式[1,20]: {legacy}")

    for name, rs_lo, rs_hi in RULES:
        _upsert(name, rs_lo, rs_hi)
    names = {str(r.get("name")) for r in load_sector_rules()}
    for name, _, _ in RULES:
        assert name in names, name
    print("完成。头档×RS 三档已写入 data/sector_rules/。")
    print("底座：Elig[1,15] + 夹档/无涨停/MA5<MA10<MA20 + 流通≥120亿")
    print("对照：合格榜标签内 RS 1–10 / 11–20 / ≥21")
    print("跑法：各勾选一条分别选七月 → 同一策略回测后三档对比。")


if __name__ == "__main__":
    main()
