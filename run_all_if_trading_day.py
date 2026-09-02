# -*- coding: utf-8 -*-
"""交易日盘后自动批跑入口（由 D:\\limit_up.bat 计划任务调用）。

流程：
1. 非交易日直接跳过
2. 先 wait_daily_cache_ready；失败则中止，后续全部不跑（Server酱中止通知）
3. 主流程各关键步骤失败即时 Server酱报警（不阻断后续，除非门禁）
4. 导出 before COS：wait_after_hours_rank_ready（等今日 top10）；超时则报警仍继续导出/上传
5. 导出 after_hours、上传 COS、写公众号草稿（IP 白名单失败则报警并每分钟重试）
6. 市场行情日度描绘增量
7. 结束后 Server酱汇总通知（保留）
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from typing import List, Optional, Sequence, Tuple

import akshare as ak
import pandas as pd

from utils.history_data_archive import archive_history_before


_trade_calendar_cache = None

# (脚本名, 额外参数…)
Job = Tuple[str, Sequence[str]]

# 公众号 IP 白名单专用退出码（与 tools/upload_cos_wechat_draft.py 一致）
EXIT_WECHAT_IP_WHITELIST = 64
WECHAT_RETRY_SEC = 60


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


def _job_label(script_name: str, extra_args: Sequence[str] = ()) -> str:
    extra = " ".join(str(a) for a in extra_args).strip()
    return f"{script_name} {extra}".rstrip() if extra else script_name


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


def _send_server_chan(title: str, content: str) -> None:
    try:
        from utils.server_chan_notify import send_server_chan
    except Exception as e:
        print(f"[Server酱] 导入失败: {e}")
        return
    r = send_server_chan(title, content)
    if r.get("success"):
        print(f"[Server酱] 已推送 ({r.get('source')})")
    else:
        print(f"[Server酱] 推送失败: {r.get('message')}")


def _alert_step_fail(
    day,
    *,
    step: str,
    detail: str = "",
    alert_key: str = "",
) -> None:
    """主流程关键步骤失败时即时报警（与终局汇总分开）。"""
    day_s = day.strftime("%Y-%m-%d") if hasattr(day, "strftime") else str(day)
    title = f"盘后批跑失败 {day_s} · {step}"
    lines = [
        f"**日期**：{day_s}",
        f"**步骤**：{step}",
        f"**时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if detail:
        lines.append(f"**详情**：{detail}")
    lines.append("")
    lines.append("批跑将继续后续步骤（除非为入口门禁中止）。")
    try:
        from utils.server_chan_notify import notify_alert

        r = notify_alert(
            title,
            "\n".join(lines),
            alert_key=alert_key or ("batch_fail_%s_%s" % (day_s, step))[:120],
            cooldown_sec=0,
        )
        if r.get("skipped"):
            print(f"[Server酱] 跳过（冷却）: {step}")
        elif r.get("success"):
            print(f"[Server酱] 步骤报警已推送: {step}")
        else:
            print(f"[Server酱] 步骤报警失败: {r.get('message')}")
    except Exception as e:
        print(f"[Server酱] 步骤报警异常: {e}")


def _notify_daily_batch(
    day,
    *,
    status: str,
    failed: Sequence[str],
    cos_status: Optional[str] = None,
    note: str = "",
    wechat_draft_status: Optional[str] = None,
) -> None:
    """盘后批跑结束推送 Server酱（收件人见 data/notify_server_chan.json）。"""
    day_s = day.strftime("%Y-%m-%d") if hasattr(day, "strftime") else str(day)
    ok = status == "ok" and not failed
    title = f"盘后批跑{'成功' if ok else '异常'} {day_s}"
    lines = [
        f"**日期**：{day_s}",
        f"**状态**：{status}",
        f"**结束时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
    ]
    if note:
        lines.append(f"**说明**：{note}")
    if cos_status is not None:
        lines.append(f"**COS 上传**：{cos_status}")
    if wechat_draft_status is not None:
        lines.append(f"**公众号草稿**：{wechat_draft_status}")
    if failed:
        lines.append(f"**失败项（{len(failed)}）**：")
        for name in failed[:30]:
            lines.append(f"- {name}")
        if len(failed) > 30:
            lines.append(f"- …另有 {len(failed) - 30} 项")
    else:
        lines.append("**失败项**：无")
    lines.append("")
    lines.append(
        "COS 树：https://ant-quant-data-1428892855.cos.ap-guangzhou.myqcloud.com/cos/"
    )
    lines.append(
        "离线包：https://ant-quant-data-1428892855.cos.ap-guangzhou.myqcloud.com/ant-quant-data.zip"
    )
    _send_server_chan(title, "\n".join(lines))


def _record_fail(
    failed: List[str],
    day,
    *,
    label: str,
    step: str,
    detail: str = "",
    alert: bool = True,
) -> None:
    failed.append(label)
    if alert:
        _alert_step_fail(day, step=step, detail=detail or label)


def _upload_wechat_draft_with_retry(
    script_dir: str,
    today,
    today_str: str,
) -> Tuple[int, str]:
    """上传公众号草稿；遇 IP 白名单错误先报警，再每分钟重试直至成功。"""
    wx_args = ("--date", today_str)
    label = _job_label("tools/upload_cos_wechat_draft.py", wx_args)
    attempt = 0
    whitelist_alerted = False

    while True:
        attempt += 1
        print(f"[步骤] 微信公众号草稿（第 {attempt} 次）…")
        rc = _run_job(script_dir, "tools/upload_cos_wechat_draft.py", wx_args)
        if rc == 0:
            status = "成功" if attempt == 1 else f"成功（重试 {attempt} 次后）"
            if attempt > 1:
                _send_server_chan(
                    f"公众号草稿已恢复 {today_str}",
                    f"**日期**：{today_str}\n**结果**：{status}\n"
                    f"**时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
                )
            return 0, status

        if rc == EXIT_WECHAT_IP_WHITELIST:
            if not whitelist_alerted:
                whitelist_alerted = True
                _alert_step_fail(
                    today,
                    step="公众号草稿·IP白名单",
                    detail=(
                        "当前公网 IP 不在微信公众号 IP 白名单（40164）。"
                        "请到公众平台加入白名单；脚本将每 %d 秒自动重试。"
                        % WECHAT_RETRY_SEC
                    ),
                    alert_key=f"wechat_ip_whitelist_{today_str}",
                )
            else:
                # 每 10 次提醒一次，避免刷屏
                if attempt % 10 == 0:
                    _alert_step_fail(
                        today,
                        step="公众号草稿·IP白名单仍未恢复",
                        detail=f"已重试 {attempt} 次，仍为白名单错误，继续每分钟重试。",
                        alert_key=f"wechat_ip_whitelist_remind_{today_str}",
                    )
            print(
                f"[微信草稿] IP 白名单错误，{WECHAT_RETRY_SEC} 秒后重试 "
                f"（已尝试 {attempt} 次）…"
            )
            time.sleep(WECHAT_RETRY_SEC)
            continue

        # 非白名单失败：报警一次后返回，不无限重试
        _alert_step_fail(
            today,
            step="公众号草稿",
            detail=f"{label} exit={rc}",
            alert_key=f"wechat_draft_fail_{today_str}",
        )
        return rc, f"失败 exit={rc}"


def main() -> int:
    today = datetime.now().date()
    if not is_tradeday(today):
        print(f"[跳过] {today} 不是交易日，不执行盘后批跑。")
        return 0

    script_dir = os.path.dirname(os.path.abspath(__file__))
    print(f"[执行] {today} 是交易日，开始盘后批跑。")
    failed: List[str] = []
    cos_status: Optional[str] = None
    wechat_draft_status: Optional[str] = None
    today_str = today.strftime("%Y-%m-%d")

    # 1) 先等日线；失败则整批跳过
    print("[步骤] 等待 daily_cache 今日同步完成…")
    wait_rc = _run_job(script_dir, "wait_daily_cache_ready.py")
    if wait_rc != 0:
        print("[中止] daily_cache 未就绪，跳过全部后续程序。")
        _notify_daily_batch(
            today,
            status="aborted",
            failed=["wait_daily_cache_ready.py"],
            note="daily_cache 未就绪，后续全部未跑",
        )
        return 1

    # 2) 涨停日抓取；成功后再导出当日涨停家数行业/概念排名 CSV（失败只记错，不阻断）
    time.sleep(2)
    lu_rc = _run_job(script_dir, "limit_up_sector_monitor_web.py", ("--once",))
    if lu_rc != 0:
        _record_fail(
            failed,
            today,
            label=_job_label("limit_up_sector_monitor_web.py", ("--once",)),
            step="涨停日抓取",
        )
    else:
        time.sleep(2)
        export_args = ("--date", today_str, "--force")
        export_rc = _run_job(script_dir, "tools/export_limit_up_board_rank.py", export_args)
        if export_rc != 0:
            _record_fail(
                failed,
                today,
                label=_job_label("tools/export_limit_up_board_rank.py", export_args),
                step="涨停家数排名导出",
            )

    # 3) 其余程序（顺序）— 关键步骤名用于报警标题
    jobs: List[Tuple[str, Sequence[str], str]] = [
        ("get_capital_flow_selenium.py", (), "主力资金流"),
        ("tools/snapshot_eastmoney_board_rank.py", ("--with-fund-flow",), "东财板块日快照"),
        ("sector_stock_filter.py", ("--auto-run",), "选股"),
        ("profit_index_gui.py", ("--auto-run",), "盈亏指数"),
        ("main_line_group_gui.py", ("--auto-run",), "主线分组"),
        ("limit_up_structure_analysis_gui.py", ("--auto-run",), "涨停结构"),
        ("tools/seal_rating_daily_verify.py", ("--auto-run",), "封单评级验证"),
        ("limit_up_gene_analysis_gui.py", ("--auto-run",), "涨停基因"),
        ("main_force_net_inflow_gui.py", ("--auto-run",), "主力净流入分析"),
        ("lhb_analysis_gui.py", ("--auto-run",), "龙虎榜分析"),
        ("tools/lhb_premium_daily_verify.py", ("--auto-run",), "龙虎榜溢价复盘"),
        ("inst_net_rank_gui.py", ("--auto-run",), "机构榜"),
        (
            "tools/build_stock_info_from_em_boards.py",
            ("--mode", "f10", "--no-resume", "--preview", "0"),
            "全A股票信息F10",
        ),
    ]

    print(f"[执行] 共 {len(jobs)} 个后续程序（涨停抓取与 LU 排名导出已单独执行）。")
    for script_name, extra_args, step_name in jobs:
        time.sleep(2)
        rc = _run_job(script_dir, script_name, extra_args)
        if rc != 0:
            _record_fail(
                failed,
                today,
                label=_job_label(script_name, extra_args),
                step=step_name,
                detail=f"exit={rc}",
            )

    # 4) 批跑结束后归档旧文件（无论中间是否有失败，都执行，避免目录膨胀）
    try:
        _archive_old_history(script_dir, today)
    except Exception as e:
        print(f"[归档] 失败: {e}")
        _record_fail(
            failed,
            today,
            label=f"history_archive: {e}",
            step="历史归档",
            detail=str(e),
        )

    # 4.5) 等今日盘后量能 top10（避免批跑早于 QMT 量能导致 COS 缺当日）
    print("[步骤] 等待 after_hours_rank 今日 top10 就绪…")
    ah_wait_rc = _run_job(script_dir, "wait_after_hours_rank_ready.py")
    if ah_wait_rc != 0:
        _record_fail(
            failed,
            today,
            label=_job_label("wait_after_hours_rank_ready.py"),
            step="等待盘后量能",
            detail=f"exit={ah_wait_rc}",
        )
        print("[警告] 盘后量能未就绪，仍将导出当前已有 top10 并继续上传 COS。")

    # 4.6) COS 导出兜底
    print("[步骤] 导出 after_hours → data/cos …")
    ah_rc = _run_job(script_dir, "tools/export_after_hours_top_to_jsonl.py")
    if ah_rc != 0:
        _record_fail(
            failed,
            today,
            label=_job_label("tools/export_after_hours_top_to_jsonl.py"),
            step="after_hours导出",
            detail=f"exit={ah_rc}",
        )

    # 5) 上传 COS
    print("[步骤] 上传 data/cos 到腾讯云 COS…")
    cos_args = ("--with-zip",)
    cos_rc = _run_job(script_dir, "tools/upload_cos_data.py", cos_args)
    if cos_rc == 0:
        cos_status = "成功（树 + zip）"
    else:
        cos_status = f"失败 exit={cos_rc}"
        _record_fail(
            failed,
            today,
            label=_job_label("tools/upload_cos_data.py", cos_args),
            step="COS上传",
            detail=cos_status,
        )

    # 5.5) 微信公众号草稿（白名单失败则报警并每分钟重试）
    print("[步骤] 生成并上传微信公众号 COS 日更草稿…")
    wx_rc, wechat_draft_status = _upload_wechat_draft_with_retry(
        script_dir, today, today_str
    )
    if wx_rc != 0:
        failed.append(_job_label("tools/upload_cos_wechat_draft.py", ("--date", today_str)))
        if cos_status and "成功" in str(cos_status):
            cos_status = f"{cos_status}；公众号草稿失败"
        elif not cos_status:
            cos_status = "公众号草稿失败"

    # 6) 市场行情日度描绘增量（本地 CSV，不进 COS）
    print("[步骤] 市场行情日度描绘增量…")
    regime_args = ("--incremental",)
    regime_rc = _run_job(script_dir, "tools/export_market_regime_to_csv.py", regime_args)
    if regime_rc != 0:
        _record_fail(
            failed,
            today,
            label=_job_label("tools/export_market_regime_to_csv.py", regime_args),
            step="行情描绘增量",
            detail=f"exit={regime_rc}",
        )

    # 7) Server酱终局摘要（保留）
    status = "ok" if not failed else "partial_fail"
    _notify_daily_batch(
        today,
        status=status,
        failed=failed,
        cos_status=cos_status,
        wechat_draft_status=wechat_draft_status,
    )

    if failed:
        print(f"[完成] 盘后批跑结束，存在失败项 {len(failed)} 个。")
        return 1

    print("[完成] 盘后批跑结束，全部成功。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
