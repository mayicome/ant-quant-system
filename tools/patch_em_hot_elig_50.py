# -*- coding: utf-8 -*-
"""将东财热门规则 Elig 放宽为：板块 1–50、概念 1–50（合格 Top50 全收）。"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RULE_DIR = ROOT / "data" / "sector_rules"

REPLACEMENTS = [
    ("ELIG_HI_SECTOR = 30", "ELIG_HI_SECTOR = 50"),
    ("ELIG_HI_CONCEPT = 40", "ELIG_HI_CONCEPT = 50"),
    ("ELIG_HI_CONCEPT = 30", "ELIG_HI_CONCEPT = 50"),
    ("ELIG_HI = 40  # 引擎 elig_bands 上限（覆盖概念1-40）；规则内按类型用 SECTOR/CONCEPT",
     "ELIG_HI = 50  # 引擎 elig_bands 上限（覆盖概念1-50）；规则内按类型用 SECTOR/CONCEPT"),
    ("ELIG_HI = 40  # 引擎 elig_bands 上限", "ELIG_HI = 50  # 引擎 elig_bands 上限"),
    ('HOT_MODE = "today_elig_sec1_30_con1_40_rs_1_50_frac1of2_ma_union_no_gap"',
     'HOT_MODE = "today_elig_sec1_50_con1_50_rs_1_50_frac1of2_ma_union_no_gap"'),
    ('HOT_MODE = "today_elig_sec1_30_con1_40_rs_1_50_frac1of2_ma_union"',
     'HOT_MODE = "today_elig_sec1_50_con1_50_rs_1_50_frac1of2_ma_union"'),
    ('HOT_MODE = "today_elig_sec1_30_con1_40_rs_1_50_frac1of2"',
     'HOT_MODE = "today_elig_sec1_50_con1_50_rs_1_50_frac1of2"'),
    ("合格序位不在类型档内(板块1-30/概念1-40)", "合格序位不在类型档内(板块1-50/概念1-50)"),
    ("合格榜内序位按类型（板块[1,30]/概念[1,40]）", "合格榜内序位按类型（板块[1,50]/概念[1,50]）"),
    ("合格榜内序位按类型（板块[1,30]/概念[1,30]）", "合格榜内序位按类型（板块[1,50]/概念[1,50]）"),
    ("合格榜内序位[1,30]", "合格榜内序位[1,50]"),
]


def patch_code(code: str) -> tuple[str, int]:
    n = 0
    for old, new in REPLACEMENTS:
        if old in code:
            cnt = code.count(old)
            code = code.replace(old, new)
            n += cnt
    return code, n


def main() -> int:
    if not RULE_DIR.is_dir():
        print(f"缺少目录: {RULE_DIR}", file=sys.stderr)
        return 1

    touched = 0
    for path in sorted(RULE_DIR.glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        name = str(raw.get("name") or "")
        if not name.startswith("东财热门"):
            continue
        code = str(raw.get("code") or "")
        if "ELIG_HI_SECTOR" not in code and "sec1_30_con1_40" not in code and "ELIG_HI_CONCEPT = 30" not in code:
            continue
        new_code, n = patch_code(code)
        if n == 0 and "ELIG_HI_SECTOR = 50" in code:
            continue
        if new_code == code:
            print(f"skip (no match): {path.name}")
            continue
        compile(new_code, f"<{raw.get('name')}>", "exec")
        raw["code"] = new_code
        path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        touched += 1
        print(f"patched: {raw.get('name')} ({path.name}) replacements={n}")

    if not touched:
        print("未找到可补丁的东财热门规则（可能已是 Elig50）")
        return 0

    # 同步对照母集规则（从 MA排列并集 再生成一次，确保 HOT_MODE 一致）
    import create_em_ma_union_contrast_superset_rule as contrast  # noqa: WPS433

    contrast.main()
    print("done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
