# -*- coding: utf-8 -*-
"""Patch besttest rule: 板块 Elig1-30 / 概念 Elig1-40."""
from __future__ import annotations

import json
from pathlib import Path

p = Path(__file__).resolve().parents[1] / (
    "data/sector_rules/东财热门-besttest-无涨停均线差0.5to2-MA空头排列__de4a81d4.json"
)
raw = json.loads(p.read_text(encoding="utf-8"))
code = raw["code"]

old_header = (
    "# 东财今日热门：合格 Top50（成分≥10）+ 合格榜内序位[1,30] + 合格榜标签内RS≤min(50,ceil(样本/2))\n"
)
new_header = (
    "# 东财今日热门：合格 Top50（成分≥10）+ 合格榜内序位按类型（板块[1,30]/概念[1,40]）"
    " + 合格榜标签内RS≤min(50,ceil(样本/2))\n"
)
if old_header not in code:
    raise SystemExit("header not found")
code = code.replace(old_header, new_header, 1)

old_consts = (
    "ELIG_LO = 1\n"
    "ELIG_HI = 30\n"
    "MIN_FLOAT_MV_YI = 120.0\n"
    "ANY_TAG = False\n"
    "REQUIRE_MIN_FLOAT_MV = False\n"
    "REQUIRE_MA_GAP = True\n"
    "REQUIRE_MA_LT_ALIGN = True\n"
    "REQUIRE_NO_RECENT_LU = True\n"
    "APPLY_STOCK_FILTERS = True  # 兼容：任一子开关开启即为 True\n"
    'HOT_MODE = "today_elig_1_30_rs_1_50_frac1of2"\n'
    "N = 20\n"
)

new_consts = (
    "ELIG_LO = 1\n"
    "ELIG_HI_SECTOR = 30\n"
    "ELIG_HI_CONCEPT = 40\n"
    "ELIG_HI = 40  # 引擎 elig_bands 上限（覆盖概念1-40）；规则内按类型用 SECTOR/CONCEPT\n"
    "MIN_FLOAT_MV_YI = 120.0\n"
    "ANY_TAG = False\n"
    "REQUIRE_MIN_FLOAT_MV = False\n"
    "REQUIRE_MA_GAP = True\n"
    "REQUIRE_MA_LT_ALIGN = True\n"
    "REQUIRE_NO_RECENT_LU = True\n"
    "APPLY_STOCK_FILTERS = True  # 兼容：任一子开关开启即为 True\n"
    'HOT_MODE = "today_elig_sec1_30_con1_40_rs_1_50_frac1of2"\n'
    "N = 20\n"
    "\n"
    "\n"
    "def _elig_hi_for_kind(kind):\n"
    '    k = str(kind or "").strip().lower()\n'
    '    if k in ("concept", "概念"):\n'
    "        return int(ELIG_HI_CONCEPT)\n"
    "    return int(ELIG_HI_SECTOR)\n"
)

if old_consts not in code:
    raise SystemExit("consts block not found")
code = code.replace(old_consts, new_consts, 1)

old_hot = (
    "    try:\n"
    '        elig = int(hit.get("合格榜内序位") or 0)\n'
    "    except (TypeError, ValueError):\n"
    "        elig = 0\n"
    "    if elig < int(ELIG_LO) or elig > int(ELIG_HI):\n"
    "        return False, {\n"
    '            "热门模式": HOT_MODE,\n'
    '            "合格榜内序位": elig,\n'
    '            "合格榜对应标签": hit.get("合格榜对应标签", ""),\n'
    '            "_skip": "合格序位不在[1,30]",\n'
    "        }\n"
)

new_hot = (
    "    try:\n"
    '        elig = int(hit.get("合格榜内序位") or 0)\n'
    "    except (TypeError, ValueError):\n"
    "        elig = 0\n"
    '    kind_for_elig = hit.get("合格榜标签类型", "")\n'
    "    elig_hi = _elig_hi_for_kind(kind_for_elig)\n"
    "    if elig < int(ELIG_LO) or elig > int(elig_hi):\n"
    "        return False, {\n"
    '            "热门模式": HOT_MODE,\n'
    '            "合格榜内序位": elig,\n'
    '            "合格榜对应标签": hit.get("合格榜对应标签", ""),\n'
    '            "合格榜标签类型": kind_for_elig,\n'
    '            "ELIG_HI_类型": int(elig_hi),\n'
    '            "_skip": "合格序位不在类型档内(板块1-30/概念1-40)",\n'
    "        }\n"
)

if old_hot not in code:
    raise SystemExit("hottest elig check not found")
code = code.replace(old_hot, new_hot, 1)

old_any = (
    '        elig_i = int(t.get("eligible_rank") or 0)\n'
    "        if elig_i < int(ELIG_LO) or elig_i > int(ELIG_HI):\n"
    "            n_elig_fail += 1\n"
    "            continue\n"
)

new_any = (
    '        elig_i = int(t.get("eligible_rank") or 0)\n'
    '        elig_hi_i = _elig_hi_for_kind(t.get("kind"))\n'
    "        if elig_i < int(ELIG_LO) or elig_i > int(elig_hi_i):\n"
    "            n_elig_fail += 1\n"
    "            continue\n"
)

if old_any not in code:
    raise SystemExit("anytag elig check not found")
code = code.replace(old_any, new_any, 1)

old_extra = (
    '        "ELIG_LO": int(ELIG_LO),\n'
    '        "ELIG_HI": int(ELIG_HI),\n'
)
new_extra = (
    '        "ELIG_LO": int(ELIG_LO),\n'
    '        "ELIG_HI": int(ELIG_HI),\n'
    '        "ELIG_HI_SECTOR": int(ELIG_HI_SECTOR),\n'
    '        "ELIG_HI_CONCEPT": int(ELIG_HI_CONCEPT),\n'
)
n = code.count(old_extra)
if n != 2:
    raise SystemExit("expected 2 extra ELIG blocks, got %d" % n)
code = code.replace(old_extra, new_extra)

raw["code"] = code
p.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print("updated", p)

# compile smoke
import sector_stock_filter as ssf
from datetime import date
from PyQt5.QtWidgets import QApplication
import sys

app = QApplication.instance() or QApplication(sys.argv)
rules = ssf.load_sector_rules()
hit = [r for r in rules if r.get("id") == raw["id"] or "无涨停均线差0.5to2-MA空头排列" in str(r.get("name"))]
hit = [r for r in hit if "分时" not in str(r.get("name"))]
assert hit, "rule not loaded"
r0 = hit[0]
th = ssf.SectorStockFilterThread(
    [], 20, 1, 1, 1, 1, 1, 1, rules=[r0], as_of_date=date(2026, 7, 2)
)
compiled = th._compile_rule(r0)
ns_params = compiled.get("params") or {}
print("compiled ok", r0["name"], "enabled", r0.get("enabled"))
# check constants via exec already done; probe fn globals through compile ns — params may not include ELIG
# re-exec extract: ensure helper exists by compiling again reading code
assert "ELIG_HI_SECTOR = 30" in r0["code"]
assert "ELIG_HI_CONCEPT = 40" in r0["code"]
assert "ELIG_HI = 40" in r0["code"]
assert "_elig_hi_for_kind" in r0["code"]
print("smoke ok")
