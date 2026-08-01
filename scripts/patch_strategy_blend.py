#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一次性补丁：策略 JSON 注入 tp10_blend_* 与 room_blend_start。"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATCH = """
        blend_low = float(_tp10("tp10_blend_low", 3.0))
        blend_high = float(_tp10("tp10_blend_high", 1.5))
"""

files = [
    os.path.join(ROOT, "strategy_generator_app/config/strategies/strategy_e9c83928.json"),
    os.path.join(ROOT, "strategy_generator_app/config/strategies/strategy_3dfc20c7.json"),
]

for fp in files:
    with open(fp, encoding="utf-8") as f:
        data = json.load(f)
    sp = data.setdefault("strategy_params", {})
    sp["tp10_blend_low"] = 3.0
    sp["tp10_blend_high"] = 1.5
    code = data["strategy_code"]
    if "tp10_blend_low" not in code:
        code = code.replace(
            "        if drop_high < drop_low:\n            drop_high = drop_low\n",
            "        if drop_high < drop_low:\n            drop_high = drop_low\n" + PATCH,
        )
    replacements = [
        (
            'take_profit_levels.append((1.0 + up_low * scale, r_low, drop_low * scale, f"弹性卖出-止盈+{up_low*scale*100:.1f}%·回落{drop_low*scale:g}%"))',
            'take_profit_levels.append((1.0 + up_low * scale, r_low, drop_low * scale, blend_low * scale, f"弹性卖出-止盈+{up_low*scale*100:.1f}%·回落{drop_low*scale:g}%"))',
        ),
        (
            'take_profit_levels.append((1.0 + up_high * scale, r_high, drop_high * scale, f"弹性卖出-止盈+{up_high*scale*100:.1f}%·回落{drop_high*scale:g}%"))',
            'take_profit_levels.append((1.0 + up_high * scale, r_high, drop_high * scale, blend_high * scale, f"弹性卖出-止盈+{up_high*scale*100:.1f}%·回落{drop_high*scale:g}%"))',
        ),
        (
            'take_profit_levels.append((1.0 + up_low * scale, r_low, drop_low * scale, f"弹性卖出（止盈+{up_low*scale*100:.1f}%，回落{drop_low*scale:g}%）"))',
            'take_profit_levels.append((1.0 + up_low * scale, r_low, drop_low * scale, blend_low * scale, f"弹性卖出（止盈+{up_low*scale*100:.1f}%，回落{drop_low*scale:g}%）"))',
        ),
        (
            'take_profit_levels.append((1.0 + up_high * scale, r_high, drop_high * scale, f"弹性卖出（止盈+{up_high*scale*100:.1f}%，回落{drop_high*scale:g}%）"))',
            'take_profit_levels.append((1.0 + up_high * scale, r_high, drop_high * scale, blend_high * scale, f"弹性卖出（止盈+{up_high*scale*100:.1f}%，回落{drop_high*scale:g}%）"))',
        ),
        (
            "for idx, (mult, ratio, drop_pct, rule_name) in enumerate(take_profit_levels):",
            "for idx, (mult, ratio, drop_pct, blend_pp, rule_name) in enumerate(take_profit_levels):",
        ),
        (
            '                "drop_percent": drop_pct,\n                "volume": vol,',
            '                "drop_percent": drop_pct,\n                "room_blend_start": blend_pp,\n                "volume": vol,',
        ),
        (
            "tp10_drop_low / tp10_drop_high: 触发后回落百分比（默认 2.5 / 5.0）",
            "tp10_drop_low / tp10_drop_high: 触发后回落百分比（默认 2.5 / 5.0）\\n#   - tp10_blend_low / tp10_blend_high: 距涨停pp起过渡收紧（默认 3.0 / 1.5）",
        ),
    ]
    for old, new in replacements:
        code = code.replace(old, new)
    data["strategy_code"] = code
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print("patched", os.path.basename(fp), "room_blend_start" in code)

if __name__ == "__main__":
    pass
