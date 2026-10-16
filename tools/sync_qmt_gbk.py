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
    "ant_session_local.py",
    "ant_account_snapshot.py",
    "ant_passorder.py",
    "ant_result_log.py",
    "ant_cancel_request.py",
    "ant_position_entry_dates.py",
    "ant_filled_legs.py",
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


def _read_elastic_assignments() -> str:
    """部署时把 data/config.ini [Elastic] 写进策略常量。缺段则保留源码里的数。"""
    import configparser

    cfg = configparser.ConfigParser()
    ini = ROOT / "data" / "config.ini"
    if ini.is_file():
        cfg.read(str(ini), encoding="utf-8-sig")
    defaults = {
        "ELASTIC_CONFIRM_TICKS": "4",
        "ELASTIC_COOLDOWN_AFTER_EXTREME_TICKS": "2",
        "ELASTIC_DYNAMIC_THRESHOLDS": "2",
        "ELASTIC_ROOM_TIGHT_PP": "1.0",
        "ELASTIC_DROP_TIGHT": "0.5",
        "ELASTIC_LIMIT_MINUS_PP": "1.0",
        "ELASTIC_DROP_SCALE": "0.35",
        "ELASTIC_MAX_DROP_PERCENT": "3.5",
    }
    keys = {
        "ELASTIC_CONFIRM_TICKS": "confirm_ticks",
        "ELASTIC_COOLDOWN_AFTER_EXTREME_TICKS": "cooldown_after_extreme_ticks",
        "ELASTIC_DYNAMIC_THRESHOLDS": "dynamic_thresholds",
        "ELASTIC_ROOM_TIGHT_PP": "room_tight_pp",
        "ELASTIC_DROP_TIGHT": "drop_tight",
        "ELASTIC_LIMIT_MINUS_PP": "limit_minus_pp",
        "ELASTIC_DROP_SCALE": "drop_scale",
        "ELASTIC_MAX_DROP_PERCENT": "max_drop_percent",
    }
    lines = ["# SYNC_ELASTIC_BEGIN"]
    for name, ini_key in keys.items():
        val = defaults[name]
        if cfg.has_section("Elastic"):
            raw = cfg.get("Elastic", ini_key, fallback=val).strip()
            if raw:
                val = raw
        lines.append("%s = %s" % (name, val))
    lines.append("# SYNC_ELASTIC_END")
    return "\n".join(lines)


def _inject_elastic(text: str) -> str:
    begin = "# SYNC_ELASTIC_BEGIN"
    end = "# SYNC_ELASTIC_END"
    block = _read_elastic_assignments()
    pattern = begin + r".*?" + end
    if not re.search(pattern, text, flags=re.DOTALL):
        return text
    return re.sub(pattern, lambda _m: block, text, count=1, flags=re.DOTALL)


def _to_gbk_bytes(text: str, name: str) -> bytes:
    payload = _to_gbk_text(text)
    try:
        encoded = payload.encode("gbk", errors="strict")
        encoded.decode("gbk")
    except UnicodeError as e:
        raise SyncError("%s 无法转成 QMT 所需的 GBK：%s" % (name, e)) from e
    return encoded


class SyncError(RuntimeError):
    """UTF-8 源转 GBK 失败。"""


def sync() -> list:
    """从 qmt_builtin/src（UTF-8）生成 qmt_builtin/*.py（合法 GBK）。"""
    if not SRC.is_dir():
        raise SyncError("missing qmt_builtin/src")

    data_root = str((ROOT / "data").resolve())
    written = []
    for name in PY_FILES:
        src = SRC / name
        if not src.is_file():
            print("skip", name)
            continue
        raw = src.read_text(encoding="utf-8")
        if name == "ant_qmt_paths.py":
            raw = _inject_data_root(raw, data_root)
        if name == "ant_elastic_sell_lite.py":
            raw = _inject_elastic(raw)
        out = DST / name
        encoded = _to_gbk_bytes(raw, name)
        if out.is_file() and out.read_bytes() == encoded:
            continue
        out.write_bytes(encoded)
        written.append(name)
        print("sync", out)
    return written


def main() -> None:
    try:
        written = sync()
    except SyncError as e:
        raise SystemExit(str(e)) from e
    if written:
        print("done. %d file(s). next: python tools/deploy_to_qmt.py" % len(written))
    else:
        print("unchanged. next: python tools/deploy_to_qmt.py")


if __name__ == "__main__":
    main()
