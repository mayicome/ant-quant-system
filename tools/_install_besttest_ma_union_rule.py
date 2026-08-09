# -*- coding: utf-8 -*-
"""从当前 besttest 规则克隆：Cond3 改为 MA5<MA10<MA20 ∪ MA10<MA20<MA5。"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "sector_rules" / (
    "东财热门-besttest-无涨停均线差0.5to2-MA空头排列__de4a81d4.json"
)
OUT_DIR = ROOT / "data" / "sector_rules"

OLD_CHECK = """        if not (float(ma5) < float(ma10) < float(ma20)):
            return False, {
                "热门模式": HOT_MODE,
                "MA5": round(ma5, 4),
                "MA10": round(ma10, 4),
                "MA20": round(ma20, 4),
                "_skip": "非MA5<MA10<MA20",
            }
"""

NEW_CHECK = """        _ma_ok = (float(ma5) < float(ma10) < float(ma20)) or (
            float(ma10) < float(ma20) < float(ma5)
        )
        if not _ma_ok:
            return False, {
                "热门模式": HOT_MODE,
                "MA5": round(ma5, 4),
                "MA10": round(ma10, 4),
                "MA20": round(ma20, 4),
                "_skip": "非MA5<MA10<MA20且非MA10<MA20<MA5",
            }
"""


def main() -> None:
    raw = json.loads(BASE.read_text(encoding="utf-8"))
    code = str(raw["code"])
    if code.count(OLD_CHECK) != 2:
        raise SystemExit("expected 2 MA align checks, got %d" % code.count(OLD_CHECK))
    code = code.replace(OLD_CHECK, NEW_CHECK)

    code = code.replace(
        "# 个股过滤：|MA5-MA10|/min∈[0.5%,2.0%]；近10日无涨停；MA5<MA10<MA20\n",
        "# 个股过滤：|MA5-MA10|/min∈[0.5%,2.0%]；近10日无涨停；"
        "MA5<MA10<MA20 或 MA10<MA20<MA5\n",
        1,
    )
    # keep ELIG by kind from base; tweak HOT_MODE tag if present
    code = code.replace(
        'HOT_MODE = "today_elig_sec1_30_con1_40_rs_1_50_frac1of2"',
        'HOT_MODE = "today_elig_sec1_30_con1_40_rs_1_50_frac1of2_ma_union"',
        1,
    )
    # fallback if base still old mode string
    if "ma_union" not in code and 'HOT_MODE = "today_elig_1_30_rs_1_50_frac1of2"' in code:
        code = code.replace(
            'HOT_MODE = "today_elig_1_30_rs_1_50_frac1of2"',
            'HOT_MODE = "today_elig_1_30_rs_1_50_frac1of2_ma_union"',
            1,
        )

    rid = str(uuid.uuid4())
    name = "东财热门-besttest-无涨停均线差0.5to2-MA排列并集"
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
    print("enabled True — 原 MA空头排列 规则未改；可在 GUI 中二选一启用")


if __name__ == "__main__":
    main()
