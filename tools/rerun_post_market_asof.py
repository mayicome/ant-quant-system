# -*- coding: utf-8 -*-
"""按指定交易日补跑盘后批处理（跳过 wait_daily_cache / 归档）。

用法:
  python tools/rerun_post_market_asof.py --as-of 2026-07-27
"""
from __future__ import annotations

import argparse
import os
import runpy
import subprocess
import sys
import time
from datetime import date, datetime
from typing import List, Sequence, Tuple


def _parse_asof(s: str) -> date:
    s = s.strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        return datetime.strptime(s, "%Y%m%d").date()
    return datetime.strptime(s, "%Y-%m-%d").date()


def _patch_clock(asof: date):
    """冻结系统日期到 asof（盘后 18:30），并同步 QDate.currentDate。

    返回 freezegun 的 ticker（调用方需保持引用，退出前 tick.stop()）。
    """
    from freezegun import freeze_time

    ticker = freeze_time(
        datetime(asof.year, asof.month, asof.day, 18, 30, 0),
        tick=False,
    )
    ticker.start()

    try:
        from PyQt5.QtCore import QDate

        def _qdate_today():
            return QDate(asof.year, asof.month, asof.day)

        QDate.currentDate = staticmethod(_qdate_today)  # type: ignore[method-assign, assignment]
    except Exception:
        pass
    return ticker


def _run_script(script_dir: str, script_name: str, extra_args: Sequence[str], asof: date) -> int:
    target = os.path.join(script_dir, script_name)
    if not os.path.exists(target):
        print(f"[失败] 未找到脚本: {target}")
        return 1
    # 子进程内先打补丁再 runpy，保证 GUI 取到 asof
    code = "\n".join(
        [
            "import os, runpy, sys",
            f"sys.path.insert(0, {script_dir!r})",
            f"os.chdir({script_dir!r})",
            # 先加载 pandas/numpy，再冻结时间，避免 freezegun 与二进制扩展冲突
            "import pandas as _pd  # noqa: F401",
            "import tools.rerun_post_market_asof as _r",
            f"_r._patch_clock(_r._parse_asof({asof.isoformat()!r}))",
            f"sys.argv = [{target!r}] + {list(extra_args)!r}",
            f"runpy.run_path({target!r}, run_name='__main__')",
        ]
    )
    print(f"[开始] {script_name} {' '.join(extra_args)}".rstrip())
    result = subprocess.run([sys.executable, "-X", "utf8", "-c", code], cwd=script_dir)
    print(f"[结束] {script_name} 退出码: {result.returncode}")
    return int(result.returncode or 0)


def main() -> int:
    parser = argparse.ArgumentParser(description="按指定交易日补跑盘后任务")
    parser.add_argument("--as-of", required=True, help="交易日 YYYY-MM-DD 或 YYYYMMDD")
    args = parser.parse_args()
    asof = _parse_asof(args.as_of)
    asof_dash = asof.strftime("%Y-%m-%d")
    asof_ymd = asof.strftime("%Y%m%d")

    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    os.chdir(script_dir)
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    print(f"[补跑] as-of={asof_dash} （跳过 daily_cache 等待与归档）")

    jobs: List[Tuple[str, Sequence[str]]] = [
        ("limit_up_sector_monitor_web.py", ("--once",)),
        ("get_capital_flow_selenium.py", (f"--save-date={asof_ymd}",)),
        ("sector_stock_filter.py", ("--auto-run", f"--as-of={asof_dash}")),
        ("profit_index_gui.py", ("--auto-run",)),
        ("main_line_group_gui.py", ("--auto-run",)),
        ("limit_up_structure_analysis_gui.py", ("--auto-run",)),
        ("tools/seal_rating_daily_verify.py", ("--auto-run", f"--verify-date={asof_ymd}")),
        ("limit_up_gene_analysis_gui.py", ("--auto-run",)),
        ("main_force_net_inflow_gui.py", ("--auto-run",)),
        ("lhb_analysis_gui.py", ("--auto-run",)),
        ("tools/lhb_premium_daily_verify.py", ("--auto-run", f"--verify-date={asof_ymd}")),
        # 机构榜依赖新浪龙虎榜源，偏晚更新：放到分析类任务末尾
        ("inst_net_rank_gui.py", ("--auto-run",)),
        # 与 run_all_if_trading_day 末步一致：F10 更新 all_a_stock_info.json
        ("tools/build_stock_info_from_em_boards.py", ("--mode", "f10", "--no-resume", "--preview", "0")),
    ]

    has_error = False
    for script_name, extra_args in jobs:
        time.sleep(2)
        rc = _run_script(script_dir, script_name, extra_args, asof)
        if rc != 0:
            has_error = True

    if has_error:
        print("[完成] 补跑结束，存在失败项。")
        return 1
    print("[完成] 补跑结束，全部成功。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
