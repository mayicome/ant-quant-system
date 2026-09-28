# -*- coding: utf-8 -*-
"""提交前自动更新 utils/product_version.py 的 VERSION_SUFFIX。

规则：YYYYMMDD.序号；同一天多次提交则序号 +1，换日则从 01 起。
可由 .githooks/pre-commit 调用。
"""
from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = ROOT / "utils" / "product_version.py"
SUFFIX_RE = re.compile(
    r'^(VERSION_SUFFIX\s*=\s*")(\d{8})\.(\d+)("\s*)$',
    re.MULTILINE,
)


def _next_suffix(old_date: str, old_seq: int, today: str) -> str:
    if old_date == today:
        return f"{today}.{old_seq + 1:02d}"
    return f"{today}.01"


def main() -> int:
    if not VERSION_FILE.is_file():
        print(f"[bump_version] 找不到 {VERSION_FILE}", file=sys.stderr)
        return 1
    text = VERSION_FILE.read_text(encoding="utf-8")
    m = SUFFIX_RE.search(text)
    today = datetime.now().strftime("%Y%m%d")
    if m:
        new_suffix = _next_suffix(m.group(2), int(m.group(3)), today)
        new_text = SUFFIX_RE.sub(
            rf'\g<1>{new_suffix}\g<4>',
            text,
            count=1,
        )
    else:
        # 没有标准行则追加/替换整行声明
        new_suffix = f"{today}.01"
        line = f'VERSION_SUFFIX = "{new_suffix}"\n'
        if "VERSION_SUFFIX" in text:
            new_text = re.sub(
                r'^VERSION_SUFFIX\s*=.*$',
                line.rstrip(),
                text,
                count=1,
                flags=re.MULTILINE,
            )
        else:
            new_text = text.rstrip() + "\n\n" + line
    if new_text != text:
        VERSION_FILE.write_text(new_text, encoding="utf-8", newline="\n")
        print(f"[bump_version] VERSION_SUFFIX -> {new_suffix}")
    else:
        print(f"[bump_version] VERSION_SUFFIX 已是 {new_suffix}，未改动")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
