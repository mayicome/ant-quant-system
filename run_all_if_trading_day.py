# -*- coding: utf-8 -*-
"""交易日盘后自动批跑入口（由 D:\\limit_up.bat 计划任务调用）。

流程：
1. 非交易日直接跳过
2. 先 wait_daily_cache_ready；失败则中止，后续全部不跑
3. 再顺序运行：板块监控、资金流、东财板块涨跌幅日快照、选股、各分析小程序、封单评级次日验证、龙虎榜溢价复盘
4. 最后：东财 F10 逐只更新概念/板块并入 all_a_stock_info.json（并集合并，不删旧标签）
5. 全部程序跑完后：只把 history_data **根目录**里「当天以前」的带日期文件移到 history_data/存档/（子目录不归档）
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from typing import List, Sequence, Tuple

import akshare as ak
import pandas as pd

from utils.history_data_archive import archive_history_before


_trade_calendar_cache = None

# (脚本名, 额外参数…)
Job = Tuple[str, Sequence[str]]


def _get_trade_calendar():
    """获取交易日历（带缓存）。"""
    global _trade_calendar_cache
    if _trade_calendar_cache is None:
        try:
            trade_cal = ak.tool_trade_date_hist_sina()
            trade_cal["trade_date"] = pd.to_datetime(trade_cal["trade_date"]).dt.date
            _trade_calendar_cache = set(trade_cal["trade_date"].values)
        except Exception as e:
            print(f"[交易日检查] 获取交易日历失败，回退到工作日判断: {e}")
            _trade_calendar_cache = set()
    return _trade_calendar_cache


def is_tradeday(day=None):
    """判断某天是否为交易日；默认今天。"""
    if day is None:
        day = datetime.now().date()
    if isinstance(day, datetime):
        day = day.date()
    trade_dates = _get_trade_calendar()
    if not trade_dates:
        return day.weekday() < 5
    return day in trade_dates


def _run_job(script_dir: str, script_name: str, extra_args: Sequence[str] = ()) -> int:
    target = os.path.join(script_dir, script_name)
    if not os.path.exists(target):
        print(f"[失败] 未找到脚本: {target}")
        return 1
    cmd = [sys.executable, target, *list(extra_args)]
    print(f"[开始] {script_name} {' '.join(extra_args)}".rstrip())
    result = subprocess.run(cmd, cwd=script_dir)
    print(f"[结束] {script_name} 退出码: {result.returncode}")
    return int(result.returncode or 0)


def _archive_old_history(script_dir: str, keep_day) -> None:
    """批跑结束后归档：只处理 history_data 根目录，子目录不动。"""
    history_dir = os.path.join(script_dir, "history_data")
    print(f"[归档] 将根目录中 {keep_day} 之前的文件移到存档（子目录不归档）…")
    moved, skipped, errors = archive_history_before(history_dir, keep_day)
    print(f"[归档] 已移动 {moved} 个，保留/跳过 {skipped} 个。")
    if errors:
        print(f"[归档] 有 {len(errors)} 个失败：")
        for msg in errors[:20]:
            print(f"  - {msg}")
        if len(errors) > 20:
            print(f"  …另有 {len(errors) - 20} 条")


def main() -> int:
    today = datetime.now().date()
    if not is_tradeday(today):
        print(f"[跳过] {today} 不是交易日，不执行盘后批跑。")
        return 0

    script_dir = os.path.dirname(os.path.abspath(__file__))
    print(f"[执行] {today} 是交易日，开始盘后批跑。")

    # 1) 先等日线；失败则整批跳过
    print("[步骤] 等待 daily_cache 今日同步完成…")
    wait_rc = _run_job(script_dir, "wait_daily_cache_ready.py")
    if wait_rc != 0:
        print("[中止] daily_cache 未就绪，跳过全部后续程序。")
        return 1

    # 2) 其余程序（顺序）
    jobs: List[Tuple[str, Sequence[str]]] = [
        ("limit_up_sector_monitor_web.py", ("--once",)),
        ("get_capital_flow_selenium.py", ()),
        # 选股前：东财概念+行业涨跌幅全榜日快照（失败只记错，不阻断选股）
        ("tools/snapshot_eastmoney_board_rank.py", ("--with-fund-flow",)),
        ("sector_stock_filter.py", ("--auto-run",)),
        ("profit_index_gui.py", ("--auto-run",)),
        ("main_line_group_gui.py", ("--auto-run",)),
        ("inst_net_rank_gui.py", ("--auto-run",)),
        ("limit_up_structure_analysis_gui.py", ("--auto-run",)),
        ("tools/seal_rating_daily_verify.py", ("--auto-run",)),
        ("limit_up_gene_analysis_gui.py", ("--auto-run",)),
        ("main_force_net_inflow_gui.py", ("--auto-run",)),
        ("lhb_analysis_gui.py", ("--auto-run",)),
        ("tools/lhb_premium_daily_verify.py", ("--auto-run",)),
        # 每日最后：F10 逐只更新概念/板块并并入 all_a_stock_info.json
        # --no-resume：每日重拉；--mode f10：避开易断的 push2 板块成分接口
        ("tools/build_stock_info_from_em_boards.py", ("--mode", "f10", "--no-resume", "--preview", "0")),
    ]

    print(f"[执行] 共 {len(jobs)} 个后续程序。")
    has_error = False
    for script_name, extra_args in jobs:
        time.sleep(2)
        rc = _run_job(script_dir, script_name, extra_args)
        if rc != 0:
            has_error = True

    # 3) 批跑结束后归档旧文件（无论中间是否有失败，都执行，避免目录膨胀）
    try:
        _archive_old_history(script_dir, today)
    except Exception as e:
        print(f"[归档] 失败: {e}")
        has_error = True

    if has_error:
        print("[完成] 盘后批跑结束，存在失败项。")
        return 1

    print("[完成] 盘后批跑结束，全部成功。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
