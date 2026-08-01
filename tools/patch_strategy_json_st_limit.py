#!/usr/bin/env python3
"""补丁：策略 JSON 中 tp10 的 ST 档位缩放（2026-07-06 起主板 ST 按 10%）。"""
import glob
import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OLD = '''        if "ST" in name_upper.upper():
            limit_type = "5%"
            scale = 0.5
        elif code_6.startswith(("300", "301", "688", "689")):
            limit_type = "20%"
            scale = 2.0
        else:
            limit_type = "10%"
            scale = 1.0'''

NEW = '''        from datetime import date as _st_limit_date
        if code_6.startswith(("300", "301", "688", "689")):
            limit_type = "20%"
            scale = 2.0
        elif (
            "ST" in name_upper.upper()
            and _st_limit_date.today() < _st_limit_date(2026, 7, 6)
            and not code_6.startswith(("8", "4", "920"))
        ):
            limit_type = "5%"
            scale = 0.5
        else:
            limit_type = "10%"
            scale = 1.0'''


def main() -> None:
    base = os.path.join(_ROOT, "strategy_generator_app", "config", "strategies")
    for path in glob.glob(os.path.join(base, "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError:
            print("skip (invalid json):", os.path.basename(path))
            continue
        code = data.get("strategy_code", "")
        if OLD not in code:
            continue
        data["strategy_code"] = code.replace(OLD, NEW)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print("patched:", os.path.basename(path))


if __name__ == "__main__":
    main()
