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

CAPITAL_FLOW_RETRY_SEC = 60

# 东财风控拖动验证页（与 eastmoney_fund_flow Referer 一致）

EASTMONEY_ZJLX_URL = "https://data.eastmoney.com/zjlx/detail.html"

# 同一次资金流阻塞重试里默认只弹一次浏览器；距上次打开超过该秒数才允许再弹（防每 60s 刷屏）

CAPITAL_FLOW_BROWSER_REOPEN_SEC = 30 * 60







def _open_eastmoney_zjlx_for_captcha(reason: str = "") -> None:

    """打开东财个股资金流页，便于用户拖动真人验证条解风控。失败只打日志，不抛。"""

    try:

        import webbrowser



        ok = webbrowser.open(EASTMONEY_ZJLX_URL)

        tip = f"（{reason}）" if reason else ""

        print(f"[资金流] 已打开东财资金流页供拖动验证{tip}: {EASTMONEY_ZJLX_URL} (ok={ok})")

    except Exception as exc:

        print(f"[资金流] 打开东财网页失败（不影响重试）: {exc}")





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

        PipelineStep("qmt_quote", "导出沪深京行情（data/qmt_hsj_quote.txt）", selectable=True),

        PipelineStep("daily_cache", "下载日线缓存", is_gate=True, selectable=True),

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







def _subprocess_with_ipv4_cmd(

    script_dir: str,

    target: str,

    extra_args: Sequence[str] = (),

    *,

    asof: Optional[date] = None,

) -> List[str]:

    """Build ``python -c`` that installs prefer_ipv4 then runs the target script.



    Subprocess does not inherit the parent process socket monkeypatch, so each

    child must install IPv4 preference itself.

    """

    code_lines = [

        "import os, runpy, sys",

        f"sys.path.insert(0, {script_dir!r})",

        f"os.chdir({script_dir!r})",

        "try:",

        "    from utils.http_ipv4 import install_prefer_ipv4",

        "    install_prefer_ipv4()",

        "except Exception:",

        "    pass",

    ]

    if asof is not None:

        code_lines.extend(

            [

                "import pandas as _pd  # noqa: F401",

                "import tools.rerun_post_market_asof as _r",

                f"_r._patch_clock(_r._parse_asof({asof.isoformat()!r}))",

            ]

        )

    code_lines.extend(

        [

            f"sys.argv = [{target!r}] + {list(extra_args)!r}",

            f"runpy.run_path({target!r}, run_name='__main__')",

        ]

    )

    return [sys.executable, "-X", "utf8", "-c", "\n".join(code_lines)]





def _run_plain(script_dir: str, script_name: str, extra_args: Sequence[str] = ()) -> int:

    target = os.path.join(script_dir, script_name)

    if not os.path.exists(target):

        print(f"[失败] 未找到脚本: {target}")

        return 1

    cmd = _subprocess_with_ipv4_cmd(script_dir, target, extra_args)

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

    cmd = _subprocess_with_ipv4_cmd(script_dir, target, extra_args)

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

    cmd = _subprocess_with_ipv4_cmd(script_dir, target, extra_args, asof=asof)

    print(f"[开始][asof={asof}] {script_name} {' '.join(map(str, extra_args))}".rstrip())

    result = subprocess.run(cmd, cwd=script_dir)

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

    capital_flow_auto_retry: bool = True,

    stop_capital_flow_retry: Optional[Callable[[], bool]] = None,

    capital_flow_selenium_fallback: bool = False,

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

        if step_id == "qmt_quote":

            rc = run(

                "tools/export_qmt_hsj_quote.py",

                ("--date", d, "--click"),

            )

            if rc != 0:

                detail = "请先打开并登录国金 QMT 客户端（保持运行即可；脚本会自动切到「行情 → 沪深京A股」）。产物：data/qmt_hsj_quote.txt"

            else:

                detail = "写出 data/qmt_hsj_quote.txt（及 .meta.json、a_share_universe.json）"

            return StepRunResult(ok=rc == 0, exit_code=rc, detail=detail)



        if step_id == "daily_cache":
            # 仅实盘（data/qmt_live_only.flag / --live-only）：外源东财/腾讯/同花顺（原路径）
            # 全量机：国金 QMT 探针分片（需模型交易里日线探针在跑）
            from utils.live_only import is_live_only_machine
            from utils.trading_day import previous_tradeday

            use_external = bool(live_only) or is_live_only_machine(root)
            if use_external:
                prev = previous_tradeday(day).strftime("%Y-%m-%d")
                rc = run(
                    "tools/append_daily_cache_external.py",
                    ("--date", d, "--prev", prev),
                )
                detail = (
                    "仅实盘→外源日线(+ETF自2025-01-01)：追加+目标日校对+上回补；"
                    "覆盖需多源一致或半截K"
                )
                return StepRunResult(ok=rc == 0, exit_code=rc, detail=detail)

            rc = run(
                "tools/run_qmt_daily_cache.py",
                (
                    "--date",
                    d,
                    "--shard-size",
                    "100",
                    "--shard-interval",
                    "3",
                ),
                freeze=False,
            )
            detail = (
                "全量→QMT探针日线分片(shard=100,interval=3s)->daily_cache；"
                "请保持国金QMT模型交易探针运行；失败可手工改跑 append_daily_cache_external"
            )
            return StepRunResult(ok=rc == 0, exit_code=rc, detail=detail)



        if step_id == "limit_up":

            from utils.limit_up_day_path import ensure_limit_up_day_data_dir



            ensure_limit_up_day_data_dir(os.path.join(root, "history_data"))

            # 按目标交易日下载。非交易日补跑昨天时不要冻日历：

            # 缺 freezegun 会在抓取前退出，界面只能看到「文件缺失」。

            rc = run(

                "limit_up_sector_monitor_web.py",

                ("--once", "--date", d),

                freeze=False,

            )

            return _ok_after_run(step_id, day, rc)



        if step_id == "lu_board_rank":

            rc = run(

                "tools/export_limit_up_board_rank.py",

                ("--date", d, "--force"),

                freeze=False,

            )

            return _ok_after_run(step_id, day, rc)



        if step_id == "capital_flow":

            # 默认仅东财 push2（不同花顺）：先 >=3000 万早停落盘，再尝试全量。

            # 定时/自动：失败 Server酱 + 打开 zjlx 拖验证，每 CAPITAL_FLOW_RETRY_SEC 重试 push2。

            # 手动版（capital_flow_selenium_fallback=True）：push2 失败后改走 --selenium（>=3000 早停保底），不发 Server酱；

            # 仍不同花顺；Selenium 失败则返回，不再 60s 死循环。

            from utils.server_chan_notify import notify_alert



            def _run_flow(extra_args: tuple = ()) -> StepRunResult:

                args = (f"--save-date={d8}",) + tuple(extra_args)

                rc = run(

                    "get_capital_flow_selenium.py",

                    args,

                    freeze=False,

                )

                return _ok_after_run(step_id, day, rc)



            # 1) push2

            # push2：--min-amount=3000 表示先保子集再尝试全量（见 get_capital_flow_selenium）
            last = _run_flow(("--min-amount=3000",))

            if last.ok:

                return last



            detail = last.detail or f"exit={last.exit_code}"



            # 手动：push2 失败 → 网页 Selenium 按 >=3000 早停保底（--min-amount=3000）

            if capital_flow_selenium_fallback:

                if stop_capital_flow_retry and stop_capital_flow_retry():

                    return StepRunResult(

                        ok=False,

                        exit_code=last.exit_code,

                        detail="已停止资金流（push2 失败，未跑 Selenium）",

                    )

                _open_eastmoney_zjlx_for_captcha("push2失败，改用Selenium")


                # 手动补跑不发 Server酱，控制台日志即可


                print(f"[资金流] push2 失败（{detail}），改用 --selenium 网页翻页（>=3000早停）…")

                sel = _run_flow(("--selenium", "--min-amount=3000"))

                if sel.ok:

                    return StepRunResult(

                        ok=True,

                        exit_code=0,

                        detail=(sel.detail or "") + "（Selenium网页翻页）",

                    )

                return StepRunResult(

                    ok=False,

                    exit_code=sel.exit_code,

                    detail=(

                        f"push2失败后 Selenium 仍失败：{sel.detail or sel.exit_code}"

                        f"；先前 push2：{detail}"

                    ),

                )

            # 定时等：push2 失败后每 CAPITAL_FLOW_RETRY_SEC 重试；

            # 等待期间若本地 CSV 已通过齐全校验（手动/其它进程写入），直接放行，不再空转 push2。

            if not capital_flow_auto_retry:

                return last



            def _local_capital_flow_ready() -> Optional[StepRunResult]:

                """本地产物有效才放行：非空 + 有数据行（走 check_capital_flow）。"""

                try:

                    from utils.post_market_completeness import check_capital_flow



                    ok, det = check_capital_flow(day)

                except Exception as e:

                    print(f"[资金流] 本地产物有效性检查异常（忽略，继续 push2 重试）: {e}")

                    return None

                if not ok:

                    return None

                return StepRunResult(

                    ok=True,

                    exit_code=0,

                    detail=f"{det}（本地产物已有效，跳过 push2 重试）",

                )



            def _wait_retry_or_local(seconds: float) -> Optional[StepRunResult]:

                """分段等待；期间发现有效本地 CSV 则立即返回成功。"""

                deadline = time.time() + float(seconds)

                poll = 5.0

                while True:

                    if stop_capital_flow_retry and stop_capital_flow_retry():

                        return StepRunResult(

                            ok=False,

                            exit_code=last.exit_code,

                            detail="已停止资金流自动重试",

                        )

                    ready = _local_capital_flow_ready()

                    if ready is not None:

                        print(f"[资金流] {ready.detail}")

                        return ready

                    now = time.time()

                    if now >= deadline:

                        return None

                    time.sleep(min(poll, deadline - now))



            attempt = 1

            alerted = False

            last_browser_open_at = 0.0

            while True:

                if stop_capital_flow_retry and stop_capital_flow_retry():

                    return StepRunResult(

                        ok=False,

                        exit_code=last.exit_code,

                        detail="已停止资金流自动重试",

                    )

                # 每次重试前先看本地是否已有有效产物（手动补齐）

                ready = _local_capital_flow_ready()

                if ready is not None:

                    print(f"[资金流] {ready.detail}")

                    return ready



                detail = last.detail or f"exit={last.exit_code}"

                if not alerted:

                    alerted = True

                    _open_eastmoney_zjlx_for_captcha("首次失败")

                    last_browser_open_at = time.time()

                    notify_alert(

                        title=f"盘后批跑暂停 {d} · 主力资金流(东财)",

                        content=(

                            f"**日期**：{d}\n"

                            f"**原因**：{detail}\n"

                            f"**说明**：接口 push2 失败，多为限流/需滑块验证。"

                            f"已尝试打开 {EASTMONEY_ZJLX_URL}，请在网页完成滑块验证后关闭。"

                            f"批跑停在本步骤，每 {CAPITAL_FLOW_RETRY_SEC} 秒自动重试；"

                            f"若手动补齐且本地 CSV 校验通过，将自动继续后续步骤。"

                            f"成功前不会跑后续步骤。\n"

                            f"**时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

                        ),

                        alert_key=f"capital_flow_em_{d8}",

                        cooldown_sec=0,

                    )

                elif attempt % 10 == 0:

                    notify_alert(

                        title=f"盘后批跑仍暂停 {d} · 主力资金流(东财)",

                        content=(

                            f"已连续 {attempt} 次仍失败（{detail}）。"

                            f"仍按每 {CAPITAL_FLOW_RETRY_SEC}s 重试。"

                            f"可自检滑块验证或打开 {EASTMONEY_ZJLX_URL} 再试一次；"

                            f"或手动补齐资金流，定时检测到有效 CSV 后会自动继续。"

                        ),

                        alert_key=f"capital_flow_em_remind_{d8}",

                        cooldown_sec=0,

                    )

                    if (time.time() - last_browser_open_at) >= CAPITAL_FLOW_BROWSER_REOPEN_SEC:

                        _open_eastmoney_zjlx_for_captcha(f"提醒第{attempt}次")

                        last_browser_open_at = time.time()



                print(

                    f"[资金流] 接口失败（{detail}），"

                    f"{CAPITAL_FLOW_RETRY_SEC}s 内轮询本地有效产物，否则重试 push2"

                    f"（第 {attempt} 次）…"

                )

                waited = _wait_retry_or_local(CAPITAL_FLOW_RETRY_SEC)

                if waited is not None:

                    return waited

                attempt += 1

                last = _run_flow(("--min-amount=3000",))

                if last.ok:

                    return StepRunResult(

                        ok=True,

                        exit_code=0,

                        detail=(last.detail or "") + f"（第{attempt}次成功）",

                    )



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

            moved, skipped, errors = archive_history_before(

                history_dir, day, exclude_prefixes=("选股结果",)

            )

            detail = f"moved={moved} skipped={skipped} errors={len(errors)}"

            return StepRunResult(ok=not errors, exit_code=1 if errors else 0, detail=detail)



        if step_id == "after_hours_wait":

            return StepRunResult(ok=True, exit_code=0, detail="盘后量能已停用")



        if step_id == "after_hours_export":

            return StepRunResult(ok=True, exit_code=0, detail="盘后量能已停用")



        if step_id == "final_notify":

            return StepRunResult(ok=True, exit_code=0, detail="由 GUI/CLI 在批末调用")



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



        return StepRunResult(ok=False, exit_code=1, detail=f"未知步骤: {step_id}")

    except Exception as e:

        return StepRunResult(ok=False, exit_code=1, detail=str(e))

