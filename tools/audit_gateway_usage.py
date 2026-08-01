"""审计主交易系统是否仍有绕过 BrokerGateway 的 MiniQMT 调用。

用法:
  python tools/audit_gateway_usage.py

退出码 0 = 主路径无漏网；非 0 = 发现需关注的直接访问。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 调用方目录：应通过 qmt_adapter(BrokerGateway) 访问，不应摸 subscribe_thread
CALLER_PREFIXES = ("ui/", "core/", "main.py")

# 实现层：允许内部使用 subscribe_thread / QMTManager
IMPL_PREFIXES = ("brokers/",)

IGNORE_FILES = {
    "ui/dialogs_backup.py",
}

# 允许直连 xtquant 的调用方文件（元数据 / 日K / 独立功能）
ALLOW_XTQUANT_CALLERS = {
    "ui/limit_up_dialog.py",
    "ui/limit_up_near_dialog.py",
    "ui/limit_up_no_new_high_dialog.py",
    "ui/break_upper_band_no_new_high_dialog.py",
    "ui/break_ma_monitor_dialog.py",
    "ui/simplified_threshold_calculator.py",
    "ui/backtest_window_ext.py",
    "ui/tasks_charts_view.py",  # get_instrument_detail 查名称
    "core/backtest_engine.py",
    "core/backtest_engine_old.py",
    "core/break_ma_monitor.py",
    "core/stock_analyzer.py",
}

LEAK_PATTERNS = [
    ("subscribe_thread._stock_codes", re.compile(r"subscribe_thread\._stock_codes")),
    ("subscribe_thread.update_subscribe_list", re.compile(r"subscribe_thread\.update_subscribe_list")),
    ("qmt_adapter._is_initialized", re.compile(r"qmt_adapter\._is_initialized")),
    ("直接 new QMTManager", re.compile(r"(?<!\.)\bQMTManager\s*\(")),
]


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _is_caller(rel: str) -> bool:
    if rel in IGNORE_FILES:
        return False
    return rel == "main.py" or any(rel.startswith(p) for p in CALLER_PREFIXES if p != "main.py")


def _iter_files() -> list[Path]:
    out: list[Path] = []
    main_py = ROOT / "main.py"
    if main_py.is_file():
        out.append(main_py)
    for folder in ("ui", "core", "brokers"):
        base = ROOT / folder
        if base.is_dir():
            out.extend(sorted(base.rglob("*.py")))
    return out


def main() -> int:
    findings: list[str] = []
    for fp in _iter_files():
        rel = _rel(fp)
        if not _is_caller(rel):
            continue
        try:
            text = fp.read_text(encoding="utf-8")
        except OSError:
            continue
        for label, pat in LEAK_PATTERNS:
            for m in pat.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                findings.append(f"  {rel}:{line_no}  [{label}]")

    print("BrokerGateway 漏网审计（调用方: ui / core / main.py）")
    if findings:
        print("\n发现漏网调用:")
        for item in findings:
            print(item)
        print(f"\n合计 {len(findings)} 处 — 应改为网关门面方法")
        return 1

    print("\n通过: 调用方未发现 subscribe_thread / _is_initialized 泄漏。")
    print("\n另: 下列模块仍直连 xtquant（已登记，属日K/元数据/独立监控，非盘中行情主链路）:")
    for rel in sorted(ALLOW_XTQUANT_CALLERS):
        print(f"  - {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
