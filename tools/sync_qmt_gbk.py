# -*- coding: utf-8 -*-
"""
1) 改 qmt_builtin/src/*.py（UTF-8）
2) python tools/sync_qmt_gbk.py     -> 生成 qmt_builtin/*.py（GBK）
3) python tools/deploy_to_qmt.py    -> 复制到 QMT 策略 python 目录
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "qmt_builtin" / "src"
DST = ROOT / "qmt_builtin"

PY_FILES = (
    "ant_qmt_paths.py",
    "ant_rules_io.py",
    "ant_shadow_strategy.py",
    "ant_tick_runner.py",
    "ant_true_breakthrough_lite.py",
    "ant_elastic_sell_lite.py",
    "ant_elastic_buy_lite.py",
    "ant_daily_sync_runner.py",
    "ant_after_hours_rank_runner.py",
    "ant_sector_sync_runner.py",
    "ant_data_sync_request.py",
    "ant_tick_cache_io.py",
    "ant_tick_full_sync_runner.py",
    "ant_server_chan.py",
    "ant_account_snapshot.py",
    "ant_passorder.py",
    "ant_position_entry_dates.py",
    "ant_filled_legs.py",
    "ant_cancel_request.py",
    "蚂蚁量化规则.py",
)


def _to_gbk_text(text: str) -> str:
    lines = text.splitlines(keepends=True)
    if not lines:
        return text
    first = lines[0]
    if "utf-8" in first.lower():
        lines[0] = "#coding:gbk\n"
    elif not first.strip().lower().startswith("#coding"):
        lines.insert(0, "#coding:gbk\n")
    return "".join(lines)


def _escape_path_for_py(path: str) -> str:
    if not path:
        return '""'
    parts = []
    buf = ""
    for ch in path:
        if ord(ch) < 128:
            buf += ch
        else:
            if buf:
                parts.append('"%s"' % buf.replace("\\", "\\\\"))
                buf = ""
            parts.append('"\\u%04x"' % ord(ch))
    if buf:
        parts.append('"%s"' % buf.replace("\\", "\\\\"))
    return " + ".join(parts)


def _inject_data_root(text: str, data_root: str) -> str:
    begin = "# SYNC_DATA_ROOT_BEGIN"
    end = "# SYNC_DATA_ROOT_END"
    expr = _escape_path_for_py(data_root)
    block = begin + "\n_DATA_ROOT = " + expr + "\n" + end
    pattern = begin + r".*?" + end
    return re.sub(pattern, lambda _m: block, text, count=1, flags=re.DOTALL)


def main() -> None:
    if not SRC.is_dir():
        raise SystemExit("missing qmt_builtin/src")

    data_root = str((ROOT / "data").resolve())

    for name in PY_FILES:
        src = SRC / name
        if not src.is_file():
            print("skip", name)
            continue
        raw = src.read_text(encoding="utf-8")
        if name == "ant_qmt_paths.py":
            raw = _inject_data_root(raw, data_root)
        out = DST / name
        out.write_bytes(_to_gbk_text(raw).encode("gbk", errors="strict"))
        print("deploy", out)

    print("done. next: python tools/deploy_to_qmt.py")


if __name__ == "__main__":
    main()
