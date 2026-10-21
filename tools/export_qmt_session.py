# -*- coding: utf-8 -*-
"""把当前 data/rules_armed.json 整份写成 QMT 启动参数。

写出 qmt_builtin/src/ant_session_local.py（不进 Git）。
不部署、不重启模型。--deploy 只覆盖 QMT 里的 ant_session_local.py。
策略已在运行时约 1 秒内重新加载，不再读 rules_armed.json。

  python tools/export_qmt_session.py
  python tools/export_qmt_session.py --deploy
  python tools/export_qmt_session.py --disable
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = ROOT / "qmt_builtin" / "src" / "ant_session_local.py"
RULES = ROOT / "data" / "rules_armed.json"

_KEEP = (
    "version",
    "trade_date",
    "tasks",
    "watch_codes",
    "strategy_pool_watch",
    "subscribe_codes",
    "orders_enabled",
    "early_order_enabled",
    "min_buy_amount",
    "buy_block_window_enabled",
    "buy_block_start",
    "buy_block_end",
)


def _render(use_session: bool, session: dict) -> str:
    payload = json.dumps(session, ensure_ascii=True, separators=(",", ":"))
    literal = json.dumps(payload, ensure_ascii=True)
    return (
        "# -*- coding: utf-8 -*-\n"
        "# 本机会话快照，由 tools/export_qmt_session.py 生成，不进 Git。\n"
        "# 覆盖到 QMT python 目录后，运行中的模型约 1 秒内重新加载。\n"
        "# 本进程已报出或已成交的同一笔不会再次执行。\n"
        "import json\n"
        "USE_SESSION = %s\n"
        "SESSION = json.loads(%s)\n"
    ) % ("True" if use_session else "False", literal)


def export_enabled(ui_boot=False) -> dict:
    if not RULES.is_file():
        raise SystemExit("缺少 %s" % RULES)
    raw = json.loads(RULES.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise SystemExit("rules_armed.json 不是对象")
    tasks = raw.get("tasks")
    if not isinstance(tasks, list):
        raise SystemExit("rules_armed.json 没有 tasks 列表，拒绝导出")
    session = {k: raw[k] for k in _KEEP if k in raw}
    session["exported_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    session["source"] = "rules_armed.json"
    if ui_boot:
        session["ui_boot"] = 1
    OUT.write_text(_render(True, session), encoding="utf-8")
    return session


def export_disabled() -> None:
    OUT.write_text(_render(False, {}), encoding="utf-8")


def deploy_session_file() -> Path:
    """把已导出的 ant_session_local.py 转成 GBK，只覆盖 QMT 里的这一份。"""
    import configparser

    if not OUT.is_file():
        raise SystemExit("还没有会话快照，请先导出")
    from tools.sync_qmt_gbk import _to_gbk_bytes

    text = OUT.read_text(encoding="utf-8")
    encoded = _to_gbk_bytes(text, OUT.name)
    gbk_path = ROOT / "qmt_builtin" / OUT.name
    gbk_path.write_bytes(encoded)
    config = ROOT / "data" / "config.ini"
    cp = configparser.ConfigParser()
    cp.read(config, encoding="utf-8")
    raw = ""
    if cp.has_section("qmt_builtin"):
        raw = cp.get("qmt_builtin", "qmt_python_dir", fallback="").strip()
    if not raw:
        raise SystemExit("data/config.ini 未设置 [qmt_builtin] qmt_python_dir")
    dst_dir = Path(raw.replace("/", "\\"))
    if not dst_dir.is_dir():
        raise SystemExit("qmt_python_dir 不存在: %s" % dst_dir)
    target = dst_dir / OUT.name
    target.write_bytes(encoded)
    sim = Path(r"D:\国金QMT交易端模拟\python")
    if sim.is_dir() and sim.resolve() != dst_dir.resolve():
        (sim / OUT.name).write_bytes(encoded)
    return target


def publish_session(task_manager=None) -> dict:
    """先按当前任务整份刷新 rules_armed，再导出并写入 QMT。"""
    if task_manager is not None and hasattr(task_manager, "_sync_rules_armed_if_builtin"):
        task_manager._sync_rules_armed_if_builtin()
    session = export_enabled()
    target = deploy_session_file()
    tasks = session.get("tasks") or []
    enabled = sum(1 for t in tasks if isinstance(t, dict) and t.get("enabled", True))
    return {
        "tasks": len(tasks),
        "enabled": enabled,
        "trade_date": session.get("trade_date"),
        "target": str(target),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="导出 QMT 启动会话参数")
    parser.add_argument("--disable", action="store_true", help="关掉会话模式，恢复文件轮询")
    parser.add_argument("--deploy", action="store_true", help="导出后只覆盖 QMT 里的会话文件")
    args = parser.parse_args()
    if args.disable:
        export_disabled()
        print("[export] USE_SESSION=False -> %s" % OUT)
        if args.deploy:
            target = deploy_session_file()
            print("[export] 已写入 %s 。重启模型后恢复文件轮询。" % target)
        return
    if args.deploy:
        info = publish_session()
        print(
            "[export] 已部署 tasks=%d enabled=%d trade_date=%s -> %s"
            % (info["tasks"], info["enabled"], info["trade_date"], info["target"])
        )
        print("[export] 策略正在运行时约 1 秒内自行重载，不用重启。")
        return
    session = export_enabled()
    tasks = session.get("tasks") or []
    enabled = sum(1 for t in tasks if isinstance(t, dict) and t.get("enabled", True))
    print(
        "[export] USE_SESSION=True tasks=%d enabled=%d trade_date=%s -> %s"
        % (len(tasks), enabled, session.get("trade_date"), OUT)
    )
    print("[export] 尚未部署。实盘仍走已加载的会话，直到 --deploy 写入 QMT。")


if __name__ == "__main__":
    main()
