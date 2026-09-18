# -*- coding: utf-8 -*-
"""盘后批跑步骤定义与执行（供 CLI / GUI 共用）。"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Callable, List, Optional, Sequence, Tuple

# 公众号 IP 白名单
EXIT_WECHAT_IP_WHITELIST = 64
WECHAT_RETRY_SEC = 60


@dataclass
class PipelineStep:
    id: str
    name: str
    # 是否出现在手动「齐全度/重跑」面板（归档等过程项可 False）
    selectable: bool = True
    # 定时自动流里是否跑
    in_scheduled: bool = True
    # 仅全量模式
    full_only: bool = False
    # 日线门禁：失败则中止后续自动流
    is_gate: bool = False
    # 手动模式默认是否勾选重跑（未齐全时勾选）
    default_rerun_if_missing: bool = True


@dataclass
class StepRunResult:
    ok: bool
    exit_code: int = 0
    detail: str = ""
    skipped: bool = False


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def resolve_asof_day(now: Optional[datetime] = None) -> date:
    """手动模式目标交易日。

    - 非交易日 → 上一交易日
    - 交易日 15:00 前 → 上一交易日（当日盘后数据尚未齐）
    - 交易日 15:00 起 → 当天
    """
    from utils.trading_day import (
        MARKET_CLOSE_TIME,
        is_tradeday,
        previous_tradeday,
    )

    dt = now or datetime.now()
    day = dt.date()
    if not is_tradeday(day):
        return previous_tradeday(day)
    if dt.time() < MARKET_CLOSE_TIME:
        return previous_tradeday(day)
    return day


def build_step_catalog(*, live_only: bool = False) -> List[PipelineStep]:
    steps: List[PipelineStep] = [
        PipelineStep("daily_cache", "等待日线缓存就绪", is_gate=True, selectable=True),
        PipelineStep("limit_up", "涨停日抓取", selectable=True),
        PipelineStep("lu_board_rank", "涨停家数排名导出", selectable=True),
        PipelineStep("capital_flow", "主力资金流", selectable=True),
        PipelineStep("board_snapshot", "东财板块日快照", selectable=True),
        PipelineStep("stock_filter", "选股", selectable=True),
        PipelineStep("profit_index", "盈亏指数", full_only=True),
        PipelineStep("main_line_group", "主线分组", full_only=True),
        PipelineStep("limit_up_structure", "涨停结构", full_only=True),
        PipelineStep("seal_rating", "封单评级验证", full_only=True),
        PipelineStep("limit_up_gene", "涨停基因", full_only=True),
        PipelineStep("main_force_net_inflow", "主力净流入分析", full_only=True),
        PipelineStep("lhb_analysis", "龙虎榜分析", full_only=True),
        PipelineStep("lhb_premium", "龙虎榜溢价复盘", full_only=True),
        PipelineStep("inst_net_rank", "机构榜", full_only=True),
        PipelineStep("stock_info_f10", "全A股票信息F10", selectable=True),
        PipelineStep(
            "history_archive",
            "历史归档",
            selectable=False,
            in_scheduled=True,
            default_rerun_if_missing=False,
        ),
        PipelineStep("after_hours_wait", "等待盘后量能", full_only=True),
        PipelineStep("after_hours_export", "导出盘后量能到COS目录", full_only=True),
        PipelineStep("cos_upload", "上传腾讯云COS", full_only=True),
        PipelineStep("wechat_draft", "微信公众号草稿", full_only=True),
        PipelineStep("market_regime", "市场行情描绘增量", full_only=True),
        PipelineStep(
            "final_notify",
            "Server酱终局通知",
            selectable=False,
            default_rerun_if_missing=False,
        ),
    ]
    if live_only:
        return [s for s in steps if not s.full_only]
    return steps


def _safe_console_print(text: str) -> None:
    s = str(text or "")
    try:
        print(s)
        return
    except UnicodeEncodeError:
        pass
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    try:
        sys.stdout.buffer.write((s + "\n").encode(enc, errors="replace"))
        sys.stdout.buffer.flush()
    except Exception:
        try:
            print(s.encode("ascii", errors="replace").decode("ascii"))
        except Exception:
            pass


def _run_plain(script_dir: str, script_name: str, extra_args: Sequence[str] = ()) -> int:
    target = os.path.join(script_dir, script_name)
    if not os.path.exists(target):
        print(f"[失败] 未找到脚本: {target}")
        return 1
    cmd = [sys.executable, target, *list(extra_args)]
    print(f"[开始] {script_name} {' '.join(map(str, extra_args))}".rstrip())
    result = subprocess.run(cmd, cwd=script_dir)
    print(f"[结束] {script_name} 退出码: {result.returncode}")
    return int(result.returncode or 0)


def _run_plain_capture(
    script_dir: str, script_name: str, extra_args: Sequence[str] = ()
) -> Tuple[int, str]:
    """执行脚本并捕获 stdout/stderr（仍打印到控制台）。"""
    target = os.path.join(script_dir, script_name)
    if not os.path.exists(target):
        print(f"[失败] 未找到脚本: {target}")
        return 1, ""
    cmd = [sys.executable, target, *list(extra_args)]
    print(f"[开始] {script_name} {' '.join(map(str, extra_args))}".rstrip())
    result = subprocess.run(
        cmd,
        cwd=script_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (result.stdout or "") + (
        ("\n" + result.stderr) if result.stderr else ""
    )
    if out.strip():
        _safe_console_print(out.rstrip())
    print(f"[结束] {script_name} 退出码: {result.returncode}")
    return int(result.returncode or 0), out


def _parse_wechat_public_ip(output: str) -> str:
    import re

    m = re.search(r"public_ip\s*=\s*([0-9.]+)", output or "", re.I)
    if m:
        return m.group(1).strip()
    m = re.search(r"当前公网IP\s*=\s*([0-9.]+)", output or "")
    if m:
        return m.group(1).strip()
    return ""


def _run_asof(
    script_dir: str,
    script_name: str,
    extra_args: Sequence[str],
    asof: date,
) -> int:
    """子进程内冻结日期到 asof 再执行（复用 rerun_post_market_asof 补丁）。"""
    target = os.path.join(script_dir, script_name)
    if not os.path.exists(target):
        print(f"[失败] 未找到脚本: {target}")
        return 1
    code = "\n".join(
        [
            "import os, runpy, sys",
            f"sys.path.insert(0, {script_dir!r})",
            f"os.chdir({script_dir!r})",
            "import pandas as _pd  # noqa: F401",
            "import tools.rerun_post_market_asof as _r",
            f"_r._patch_clock(_r._parse_asof({asof.isoformat()!r}))",
            f"sys.argv = [{target!r}] + {list(extra_args)!r}",
            f"runpy.run_path({target!r}, run_name='__main__')",
        ]
    )
    print(f"[开始][asof={asof}] {script_name} {' '.join(map(str, extra_args))}".rstrip())
    result = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", code], cwd=script_dir
    )
    print(f"[结束] {script_name} 退出码: {result.returncode}")
    return int(result.returncode or 0)


def _ok_after_run(
    step_id: str,
    day: date,
    rc: int,
    *,
    detail: str = "",
    require_artifact: bool = True,
) -> StepRunResult:
    """进程退出码 +（可选）目标日产物校验。退出0但缺文件仍判失败。"""
    if rc != 0:
        return StepRunResult(
            ok=False, exit_code=rc, detail=detail or f"exit={rc}"
        )
    if not require_artifact:
        return StepRunResult(ok=True, exit_code=0, detail=detail)
    try:
        from utils.post_market_completeness import COMPLETENESS_CHECKERS

        fn = COMPLETENESS_CHECKERS.get(step_id)
        if fn is None:
            return StepRunResult(ok=True, exit_code=0, detail=detail)
        ok, det = fn(day)
    except Exception as e:
        return StepRunResult(
            ok=False, exit_code=1, detail=f"产物校验异常: {e}"
        )
    if not ok:
        return StepRunResult(
            ok=False,
            exit_code=1,
            detail=f"进程退出0但产物缺失: {det}",
        )
    return StepRunResult(ok=True, exit_code=0, detail=detail or det)


def run_step(
    step_id: str,
    *,
    script_dir: Optional[str] = None,
    asof: Optional[date] = None,
    live_only: bool = False,
    use_asof_freeze: bool = False,
    wechat_auto_retry: bool = True,
    stop_wechat_retry: Optional[Callable[[], bool]] = None,
    sleep_between: float = 0.0,
) -> StepRunResult:
    """执行单步。use_asof_freeze=True 时对依赖「今天」的脚本冻结日历。"""
    root = script_dir or project_root()
    day = asof or date.today()
    d = day.strftime("%Y-%m-%d")
    d8 = day.strftime("%Y%m%d")

    def run(script: str, args: Sequence[str] = (), *, freeze: bool = False) -> int:
        if sleep_between > 0:
            time.sleep(sleep_between)
        if freeze and use_asof_freeze:
            return _run_asof(root, script, args, day)
        return _run_plain(root, script, args)

    try:
        if step_id == "daily_cache":
            # 手动 asof：只做一次就绪检查，不长时间轮询
            if use_asof_freeze:
                from utils.post_market_completeness import check_daily_cache

                ok, detail = check_daily_cache(day)
                return StepRunResult(ok=ok, exit_code=0 if ok else 1, detail=detail)
            rc = run("wait_daily_cache_ready.py")
            return StepRunResult(ok=rc == 0, exit_code=rc)

        if step_id == "limit_up":
            rc = run("limit_up_sector_monitor_web.py", ("--once",), freeze=True)
            return _ok_after_run(step_id, day, rc)

        if step_id == "lu_board_rank":
            rc = run(
                "tools/export_limit_up_board_rank.py",
                ("--date", d, "--force"),
                freeze=False,
            )
            return _ok_after_run(step_id, day, rc)

        if step_id == "capital_flow":
            args: Tuple[str, ...] = (f"--save-date={d8}",) if use_asof_freeze else ()
            rc = run("get_capital_flow_selenium.py", args, freeze=True)
            return _ok_after_run(step_id, day, rc)

        if step_id == "board_snapshot":
            extra = ("--with-fund-flow", "--date", d)
            rc = run(
                "tools/snapshot_eastmoney_board_rank.py",
                extra,
                freeze=True,
            )
            return _ok_after_run(step_id, day, rc)

        if step_id == "stock_filter":
            # 选股已支持 --as-of，勿再 freeze_time：冻钟会导致 xtdata/超时逻辑假死，界面像卡住
            args: Tuple[str, ...] = ("--auto-run",)
            if use_asof_freeze:
                args = ("--auto-run", f"--as-of={d}")
            rc = run("sector_stock_filter.py", args, freeze=False)
            return _ok_after_run(step_id, day, rc)

        gui_map = {
            "profit_index": ("profit_index_gui.py", ("--auto-run",)),
            "main_line_group": ("main_line_group_gui.py", ("--auto-run",)),
            "limit_up_structure": ("limit_up_structure_analysis_gui.py", ("--auto-run",)),
            "limit_up_gene": ("limit_up_gene_analysis_gui.py", ("--auto-run",)),
            "main_force_net_inflow": ("main_force_net_inflow_gui.py", ("--auto-run",)),
            "lhb_analysis": ("lhb_analysis_gui.py", ("--auto-run",)),
            "inst_net_rank": ("inst_net_rank_gui.py", ("--auto-run",)),
        }
        if step_id in gui_map:
            script, args = gui_map[step_id]
            rc = run(script, args, freeze=True)
            return _ok_after_run(step_id, day, rc)

        if step_id == "seal_rating":
            args = ("--auto-run",)
            if use_asof_freeze:
                args = ("--auto-run", f"--verify-date={d8}")
            rc = run("tools/seal_rating_daily_verify.py", args, freeze=True)
            return _ok_after_run(step_id, day, rc)

        if step_id == "lhb_premium":
            args = ("--auto-run",)
            if use_asof_freeze:
                args = ("--auto-run", f"--verify-date={d8}")
            rc = run("tools/lhb_premium_daily_verify.py", args, freeze=True)
            return _ok_after_run(step_id, day, rc)

        if step_id == "stock_info_f10":
            rc = run(
                "tools/build_stock_info_from_em_boards.py",
                ("--mode", "f10", "--no-resume", "--preview", "0"),
                freeze=True,
            )
            return _ok_after_run(step_id, day, rc)

        if step_id == "history_archive":
            from utils.history_data_archive import archive_history_before

            history_dir = os.path.join(root, "history_data")
            exclude = ("选股结果",) if live_only else ()
            moved, skipped, errors = archive_history_before(
                history_dir, day, exclude_prefixes=exclude or None
            )
            detail = f"moved={moved} skipped={skipped} errors={len(errors)}"
            return StepRunResult(ok=not errors, exit_code=1 if errors else 0, detail=detail)

        if step_id == "after_hours_wait":
            if use_asof_freeze:
                from utils.post_market_completeness import check_after_hours

                ok, detail = check_after_hours(day)
                return StepRunResult(ok=ok, exit_code=0 if ok else 1, detail=detail)
            rc = run("wait_after_hours_rank_ready.py")
            return StepRunResult(ok=rc == 0, exit_code=rc)

        if step_id == "after_hours_export":
            rc = run("tools/export_after_hours_top_to_jsonl.py", freeze=True)
            return _ok_after_run(step_id, day, rc)

        if step_id == "cos_upload":
            rc = run("tools/upload_cos_data.py", ("--with-zip",))
            return _ok_after_run(step_id, day, rc)

        if step_id == "wechat_draft":
            args = ("--date", d)
            attempt = 0
            last_ip = ""
            while True:
                attempt += 1
                if sleep_between > 0:
                    time.sleep(sleep_between)
                rc, out = _run_plain_capture(
                    root, "tools/upload_cos_wechat_draft.py", args
                )
                ip = _parse_wechat_public_ip(out) or last_ip
                if ip:
                    last_ip = ip
                if rc == 0:
                    detail = "成功" if attempt == 1 else f"成功（重试 {attempt} 次）"
                    return _ok_after_run(step_id, day, 0, detail=detail)
                if rc == EXIT_WECHAT_IP_WHITELIST and wechat_auto_retry:
                    ip_txt = f"，当前公网IP={last_ip}" if last_ip else ""
                    if stop_wechat_retry and stop_wechat_retry():
                        return StepRunResult(
                            ok=False,
                            exit_code=rc,
                            detail=f"已停止自动重试（IP白名单{ip_txt}）",
                        )
                    print(
                        f"[微信草稿] IP白名单{ip_txt}，{WECHAT_RETRY_SEC}s 后重试…"
                    )
                    time.sleep(WECHAT_RETRY_SEC)
                    continue
                if rc == EXIT_WECHAT_IP_WHITELIST:
                    ip_txt = f"，当前公网IP={last_ip}" if last_ip else ""
                    return StepRunResult(
                        ok=False,
                        exit_code=rc,
                        detail=f"IP不在公众号白名单{ip_txt}",
                    )
                return StepRunResult(ok=False, exit_code=rc, detail=f"exit={rc}")

        if step_id == "market_regime":
            rc = run("tools/export_market_regime_to_csv.py", ("--incremental",), freeze=True)
            return _ok_after_run(step_id, day, rc)

        if step_id == "final_notify":
            return StepRunResult(ok=True, exit_code=0, detail="由 GUI/CLI 在批末调用")

        return StepRunResult(ok=False, exit_code=1, detail=f"未知步骤: {step_id}")
    except Exception as e:
        return StepRunResult(ok=False, exit_code=1, detail=str(e))
