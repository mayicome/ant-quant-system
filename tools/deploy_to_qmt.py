# -*- coding: utf-8 -*-
"""把 qmt_builtin/*.py（GBK）复制到大 QMT 策略 python 目录。"""
from __future__ import annotations

import configparser
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "qmt_builtin"
CONFIG = ROOT / "data" / "config.ini"

MODULE_FILES = (
    "ant_qmt_paths.py",
    "ant_rules_io.py",
    "ant_shadow_strategy.py",
    "ant_tick_runner.py",
    "ant_true_breakthrough_lite.py",
    "ant_elastic_sell_lite.py",
    "ant_elastic_buy_lite.py",
    "ant_daily_sync_runner.py",
    "ant_hfq_bulk_runner.py",
    "ant_after_hours_rank_runner.py",
    "ant_sector_sync_runner.py",
    "ant_data_sync_request.py",
    "ant_tick_cache_io.py",
    "ant_tick_full_sync_runner.py",
    "ant_server_chan.py",
    "ant_account_snapshot.py",
    "ant_passorder.py",
    "ant_position_entry_dates.py",
    "ant_speed_probe.py",
    "ant_filled_legs.py",
    "ant_cancel_request.py",
)
# 不复制 rules_io.py 等兼容层；仅主程序仓库内使用
ENTRY_FILE = "蚂蚁量化规则.py"


class DeployError(RuntimeError):
    """部署失败（配置缺失、目录不存在、源文件缺失或不是合法 GBK）。"""


def _same_bytes(src: Path, dst: Path) -> bool:
    if not dst.is_file():
        return False
    try:
        return src.read_bytes() == dst.read_bytes()
    except OSError:
        return False


def _assert_gbk(path: Path) -> None:
    data = path.read_bytes()
    try:
        data.decode("gbk")
    except UnicodeDecodeError as e:
        raise DeployError(
            "%s 不是合法 GBK，QMT 加载会报 SyntaxError。已拒绝复制。%s"
            % (path.name, e)
        ) from e


def _sync_from_utf8_src() -> None:
    """部署前始终从 src UTF-8 重生 GBK，避免 git/编辑器把副本弄成 UTF-8。"""
    import importlib.util

    script = ROOT / "tools" / "sync_qmt_gbk.py"
    spec = importlib.util.spec_from_file_location("sync_qmt_gbk", str(script))
    if spec is None or spec.loader is None:
        raise DeployError("无法加载 " + str(script))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    try:
        mod.sync()
    except Exception as e:
        raise DeployError("sync_qmt_gbk 失败: %s" % e) from e


def deploy() -> list:
    """先 sync GBK，再复制有变化的脚本到大 QMT python 目录。返回本次内容有变化的文件名。"""
    _sync_from_utf8_src()

    cp = configparser.ConfigParser()
    cp.read(CONFIG, encoding="utf-8")
    raw = ""
    if cp.has_section("qmt_builtin"):
        raw = cp.get("qmt_builtin", "qmt_python_dir", fallback="").strip()
    if not raw:
        raise DeployError("data/config.ini 未设置 [qmt_builtin] qmt_python_dir")
    dst = Path(raw.replace("/", "\\"))
    if not dst.is_dir():
        raise DeployError("qmt_python_dir 不存在: " + str(dst))

    names = list(MODULE_FILES) + [ENTRY_FILE]
    changed = []
    for name in names:
        src = SRC / name
        if not src.is_file():
            raise DeployError("缺少 " + str(src) + "（sync_qmt_gbk 未生成）")
        _assert_gbk(src)
        target = dst / name
        if _same_bytes(src, target):
            continue
        shutil.copy2(src, target)
        changed.append(name)
        print("updated", target)

    if changed:
        print(
            "done. %d file(s) changed. restart strategy in model trading"
            % len(changed)
        )
    else:
        print("unchanged. %d file(s) already match QMT" % len(names))
    return changed


def main() -> None:
    try:
        deploy()
    except DeployError as e:
        raise SystemExit(str(e)) from e


if __name__ == "__main__":
    main()
