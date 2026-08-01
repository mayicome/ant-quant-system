# -*- coding: utf-8 -*-
"""补全大 QMT 窗口内（约近 1 个月）缺失的全 A 日 tick 落盘。

【大 QMT ContextInfo 桥】
外挂 Python 没有策略 ContextInfo，不能直接 get_market_data_ex(subscribe=True)。
默认路径：把待补日期写入 data/tick_full_sync/manual_request.json，
由已运行的「蚂蚁量化规则」策略在 periodic_sync 里用真实 ContextInfo 执行
（见 ant_tick_full_sync_runner.process_manual_request）。

勿默认开 miniQMT / xtdata.download；仅调试可加 --allow-xtdata-download。

用法（项目根目录，先确保大 QMT 策略在跑）：

  python tools/backfill_tick_history.py --dry-run
  python tools/backfill_tick_history.py --wait-today --max-days 2
  # --max-days 0 = 不截断，提交全部候选缺失日（默认 max-days=2 只取最近 2 个）
  python tools/backfill_tick_history.py --wait-today --max-days 0 --submit-only
  # 指定区间全量入队（大 QMT ContextInfo / days 队列）：
  python tools/backfill_tick_history.py --from 20260620 --to 20260730 --max-days 0 --submit-only --wait-today
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_QMT = os.path.join(_ROOT, "qmt_builtin")
for p in (_ROOT, _QMT):
    if p not in sys.path:
        sys.path.insert(0, p)

_MANUAL_REQUEST = os.path.join(_ROOT, "data", "tick_full_sync", "manual_request.json")
_MANUAL_DONE = _MANUAL_REQUEST + ".done.json"
_RESULTS_JSON = os.path.join(_ROOT, "data", "results.json")


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _parse_ymd(s: str) -> date:
    s = str(s or "").strip().replace("-", "").replace("/", "")[:8]
    if len(s) != 8 or not s.isdigit():
        raise ValueError("bad date: %r" % s)
    return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))


def _import_runner():
    try:
        import ant_tick_full_sync_runner as runner  # type: ignore
    except ImportError:
        import qmt_builtin.ant_tick_full_sync_runner as runner  # type: ignore
    return runner


def _import_tick_io():
    try:
        import ant_tick_cache_io as tick_io  # type: ignore
    except ImportError:
        import qmt_builtin.ant_tick_cache_io as tick_io  # type: ignore
    return tick_io


def _trading_days(xtdata, runner, start: date, end: date) -> List[date]:
    """逐日用 runner._is_tradeday；无 xtdata 时按工作日回退。"""
    out: List[date] = []
    cur = start
    while cur <= end:
        try:
            if xtdata is not None:
                if runner._is_tradeday(xtdata, cur):
                    out.append(cur)
            elif cur.weekday() < 5:
                out.append(cur)
        except Exception:
            if cur.weekday() < 5:
                out.append(cur)
        cur += timedelta(days=1)
    return out


def _parquet_count(day_s: str) -> int:
    d = os.path.join(_ROOT, "data", "ticks", day_s)
    if not os.path.isdir(d):
        return 0
    n = 0
    for name in os.listdir(d):
        if name.endswith(".parquet") and not name.startswith("_"):
            n += 1
    return n


def _day_skipped_no_tick(runner, day_s: str) -> bool:
    """策略已标记：超出券商留存 / 历史空批跳过（队列可前进，不必再补）。"""
    try:
        path = runner._done_path(day_s)
    except Exception:
        path = os.path.join(_ROOT, "data", "ticks", day_s, "_full_sync_done.json")
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return bool(data.get("skipped_retention") or data.get("skipped_no_tick"))
    except Exception:
        return False


def _beyond_retention(runner, day_s: str) -> bool:
    fn = getattr(runner, "_day_beyond_tick_retention", None)
    if callable(fn):
        try:
            return bool(fn(day_s))
        except Exception:
            pass
    n = int(getattr(runner, "TICK_RETENTION_CALENDAR_DAYS", 28) or 28)
    try:
        d = _parse_ymd(day_s)
    except ValueError:
        return False
    return d < (date.today() - timedelta(days=n))


def _day_done(runner, day_s: str) -> bool:
    """完成判定：以盘上 parquet 为准；留存跳过视为已处理；done 不能掩盖稀疏假完成。"""
    if _day_skipped_no_tick(runner, day_s):
        return True
    if _beyond_retention(runner, day_s):
        return True
    n = _parquet_count(day_s)
    if n >= 4500:
        return True
    # 空/极少文件：即使有 _full_sync_done.json 也视为未完成（防假完成标记）
    if n < 1000:
        return False
    return bool(runner._day_already_done(day_s))


def _check_xtdata_alive(tick_io, xtdata) -> Tuple[bool, str]:
    fn = getattr(tick_io, "xtdata_service_status", None)
    if callable(fn):
        return fn(xtdata)
    try:
        client = xtdata.get_client()
        if client is None:
            return False, "无法连接行情服务: get_client()=None"
        ds = date.today().strftime("%Y%m%d")
        xtdata.get_trading_dates("SH", ds, ds)
        return True, "ok"
    except Exception as e:
        return False, "无法连接行情服务: %s" % e


def _strategy_alive(stale_sec: int = 180) -> Tuple[bool, str]:
    """用 results.json 心跳判断「蚂蚁量化规则」是否在跑。"""
    if not os.path.isfile(_RESULTS_JSON):
        return False, "无 data/results.json（策略未写过心跳）"
    try:
        age = time.time() - os.path.getmtime(_RESULTS_JSON)
    except OSError as e:
        return False, "results.json 不可读: %s" % e
    if age > float(stale_sec):
        return (
            False,
            "results.json 已 %.0fs 未更新（阈值 %ds）：请先在大 QMT 启动策略「蚂蚁量化规则」"
            % (age, stale_sec),
        )
    return True, "results.json age=%.0fs" % age


def _today_done_path(day_s: str) -> str:
    return os.path.join(_ROOT, "data", "ticks", day_s, "_full_sync_done.json")


def _today_progress_path(day_s: str) -> str:
    return os.path.join(_ROOT, "data", "ticks", day_s, "_full_sync_progress.json")


def _run_log_path(day_s: str) -> str:
    return os.path.join(_ROOT, "data", "tick_full_sync", "%s_run.log" % day_s)


def _today_tick_idle(day_s: str, stale_sec: int = 300) -> Tuple[bool, str]:
    done = _today_done_path(day_s)
    if os.path.isfile(done):
        return True, "done"
    prog = _today_progress_path(day_s)
    if not os.path.isfile(prog):
        n = _parquet_count(day_s)
        if n <= 0:
            return True, "not_started"
        return True, "no_progress_file parquet≈%d" % n
    try:
        age = time.time() - os.path.getmtime(prog)
    except OSError:
        return True, "progress_unreadable"
    if age <= float(stale_sec):
        return False, "running progress_age=%.0fs" % age
    return True, "stale_progress age=%.0fs" % age


def wait_today_tick_done(
    timeout_sec: int,
    interval_sec: int = 60,
    *,
    skip_wait: bool = False,
    stale_sec: int = 300,
) -> bool:
    today = _ymd(date.today())
    path = _today_done_path(today)

    if skip_wait:
        idle, reason = _today_tick_idle(today, stale_sec=stale_sec)
        print(
            "[backfill_tick] --skip-wait：不阻塞（今日 idle=%s reason=%s）"
            % (idle, reason)
        )
        return True

    idle, reason = _today_tick_idle(today, stale_sec=stale_sec)
    if idle:
        print(
            "[backfill_tick] 今日 tick 空闲（%s），直接开始补历史"
            % reason
        )
        return True

    print(
        "[backfill_tick] 等待今日 tick 完成: %s （超时 %ds；当前 %s）"
        % (path, timeout_sec, reason)
    )
    t0 = time.time()
    while True:
        if os.path.isfile(path):
            print("[backfill_tick] 今日 tick 已完成: %s" % today)
            return True
        idle, reason = _today_tick_idle(today, stale_sec=stale_sec)
        if idle and reason != "done":
            print(
                "[backfill_tick] 今日 tick 已不再活跃（%s），开始补历史"
                % reason
            )
            return True
        elapsed = int(time.time() - t0)
        n = _parquet_count(today)
        print(
            "[%s] 已等 %ds | 今日 parquet≈%d | %s"
            % (datetime.now().strftime("%H:%M:%S"), elapsed, n, reason)
        )
        if elapsed >= timeout_sec:
            print("[backfill_tick] 等待超时，中止（避免与今日同步抢行情）")
            return False
        time.sleep(max(10, interval_sec))


def list_candidate_days(
    xtdata,
    runner,
    lookback_calendar: int,
    from_d: Optional[date],
    to_d: Optional[date],
    include_today: bool,
) -> List[str]:
    today = date.today()
    end = to_d or (today if include_today else today - timedelta(days=1))
    start = from_d or (end - timedelta(days=max(7, lookback_calendar)))
    if start > end:
        start, end = end, start
    days = _trading_days(xtdata, runner, start, end)
    retention = int(getattr(runner, "TICK_RETENTION_CALENDAR_DAYS", 28) or 28)
    floor = today - timedelta(days=max(1, retention))
    out: List[str] = []
    skipped_old = 0
    for d in days:
        if (not include_today) and d == today:
            continue
        day_s = _ymd(d)
        if d < floor:
            skipped_old += 1
            continue
        if _day_done(runner, day_s):
            continue
        out.append(day_s)
    if skipped_old:
        print(
            "[backfill_tick] 超出券商tick留存（>%d 自然日）已跳过 %d 个候选日"
            % (retention, skipped_old)
        )
    return out


def _atomic_write_json(path: str, payload: Dict[str, Any]) -> None:
    folder = os.path.dirname(path) or "."
    if not os.path.isdir(folder):
        os.makedirs(folder)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _load_manual_request() -> Optional[Dict[str, Any]]:
    if not os.path.isfile(_MANUAL_REQUEST):
        return None
    try:
        with open(_MANUAL_REQUEST, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _normalize_days_field(req: Optional[Dict[str, Any]]) -> List[str]:
    if not req:
        return []
    out: List[str] = []
    seen = set()
    raw_days = req.get("days")
    items: List[Any] = []
    if isinstance(raw_days, list):
        items.extend(raw_days)
    elif raw_days not in (None, ""):
        items.append(raw_days)
    if req.get("day") not in (None, ""):
        items.insert(0, req.get("day"))
    for item in items:
        ds = str(item or "").replace("-", "").replace("/", "")[:8]
        if len(ds) != 8 or not ds.isdigit() or ds in seen:
            continue
        seen.add(ds)
        out.append(ds)
    return out


def submit_strategy_backfill(
    days: List[str],
    *,
    force: bool = False,
    limit: int = 0,
    merge: bool = True,
) -> Dict[str, Any]:
    """写入 manual_request.json，供大 QMT 策略用 ContextInfo 执行。"""
    fresh = [str(d).replace("-", "").replace("/", "")[:8] for d in days]
    fresh = [d for d in fresh if len(d) == 8 and d.isdigit()]
    planned = list(fresh)
    if merge:
        existing = _normalize_days_field(_load_manual_request())
        seen = set(planned)
        for d in existing:
            if d not in seen:
                seen.add(d)
                planned.append(d)

    payload = {
        "days": planned,
        "force": bool(force),
        "limit": int(limit or 0),
        "source": "backfill_tick_history",
        "requested_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        # 明确不走 miniQMT
        "enable_xtdata_download": False,
    }
    _atomic_write_json(_MANUAL_REQUEST, payload)
    return payload


def _day_started_by_strategy(day_s: str, since_ts: float) -> bool:
    """请求是否已被策略拉起：done 归档 / run.log / progress 有新写入。"""
    if os.path.isfile(_MANUAL_DONE):
        try:
            if os.path.getmtime(_MANUAL_DONE) >= since_ts - 1:
                with open(_MANUAL_DONE, "r", encoding="utf-8") as f:
                    meta = json.load(f) or {}
                if str(meta.get("day") or "")[:8] == day_s:
                    return True
                # 队列已推进到后续日，也说明策略在干活
                if since_ts - 1 <= os.path.getmtime(_MANUAL_DONE):
                    return True
        except Exception:
            pass
    logp = _run_log_path(day_s)
    if os.path.isfile(logp):
        try:
            if os.path.getmtime(logp) >= since_ts - 1:
                return True
        except OSError:
            pass
    prog = _today_progress_path(day_s)
    if os.path.isfile(prog):
        try:
            if os.path.getmtime(prog) >= since_ts - 1:
                return True
        except OSError:
            pass
    # 队首已被消费：请求文件不存在或 days 不再含该日
    req = _load_manual_request()
    if req is None:
        # 文件消失：可能已归档（单日）或尚未写——结合 since 后 done 判断
        if os.path.isfile(_MANUAL_DONE):
            try:
                if os.path.getmtime(_MANUAL_DONE) >= since_ts - 1:
                    return True
            except OSError:
                pass
    else:
        left = _normalize_days_field(req)
        if day_s not in left and os.path.getmtime(_MANUAL_REQUEST) >= since_ts - 1:
            return True
    return False


def wait_strategy_pickup(day_s: str, since_ts: float, timeout_sec: int = 120) -> bool:
    print(
        "[backfill_tick] 等待策略领取请求（%ds 内应看到 ContextInfo 开跑 %s）…"
        % (timeout_sec, day_s)
    )
    t0 = time.time()
    while time.time() - t0 < float(timeout_sec):
        if _day_started_by_strategy(day_s, since_ts):
            print("[backfill_tick] 策略已领取并用 ContextInfo 开始拉 %s" % day_s)
            return True
        alive, detail = _strategy_alive(stale_sec=300)
        print(
            "[%s] 等待领取… alive=%s (%s)"
            % (datetime.now().strftime("%H:%M:%S"), alive, detail)
        )
        time.sleep(5)
    print(
        "[backfill_tick] 策略未在 %ds 内领取 manual_request。"
        "请确认大 QMT 已加载「蚂蚁量化规则」且 periodic_sync 在跑。"
        % timeout_sec
    )
    return False


def wait_day_done(
    runner,
    day_s: str,
    *,
    timeout_sec: int,
    interval_sec: int = 30,
) -> bool:
    """轮询 parquet / _full_sync_done / run.log DONE。"""
    t0 = time.time()
    last_n = -1
    while True:
        if _day_done(runner, day_s):
            print(
                "[backfill_tick] %s 完成 parquet≈%d"
                % (day_s, _parquet_count(day_s))
            )
            return True
        logp = _run_log_path(day_s)
        if os.path.isfile(logp):
            try:
                with open(logp, "r", encoding="utf-8", errors="ignore") as f:
                    # 只看尾部，避免大文件
                    f.seek(0, os.SEEK_END)
                    size = f.tell()
                    f.seek(max(0, size - 4000), os.SEEK_SET)
                    tail = f.read()
                if "\n" + "DONE " in "\n" + tail or tail.strip().endswith("DONE") or " DONE " in tail:
                    # ABORTED 也带 DONE 前缀？看日志格式: "DONE ok=..." vs "ABORTED..."
                    for line in tail.splitlines()[::-1]:
                        if "DONE ok=" in line or line.strip().startswith("DONE"):
                            if _day_done(runner, day_s) or _parquet_count(day_s) >= 4500:
                                print("[backfill_tick] %s run.log 见 DONE" % day_s)
                                return True
                            break
                        if "ABORTED" in line or "ABORT " in line:
                            print("[backfill_tick] %s run.log 见中止: %s" % (day_s, line.strip()[:120]))
                            return False
            except Exception:
                pass
        n = _parquet_count(day_s)
        prog = _today_progress_path(day_s)
        prog_age = "-"
        if os.path.isfile(prog):
            try:
                prog_age = "%.0fs" % (time.time() - os.path.getmtime(prog))
            except OSError:
                prog_age = "?"
        if n != last_n:
            print(
                "[%s] %s 进行中 parquet≈%d progress_age=%s"
                % (datetime.now().strftime("%H:%M:%S"), day_s, n, prog_age)
            )
            last_n = n
        elapsed = int(time.time() - t0)
        if elapsed >= timeout_sec:
            print(
                "[backfill_tick] %s 等待超时（%ds），parquet≈%d"
                % (day_s, timeout_sec, n)
            )
            return False
        time.sleep(max(5, interval_sec))


def _run_via_strategy(
    runner,
    planned: List[str],
    *,
    force: bool,
    limit: int,
    pickup_timeout: int,
    day_timeout: int,
    poll_interval: int,
    require_alive: bool,
) -> int:
    alive, detail = _strategy_alive()
    if not alive:
        print("[backfill_tick] %s" % detail)
        if require_alive:
            print(
                "[backfill_tick] 请先启动：国金证券QMT交易端 → 策略「蚂蚁量化规则」→ 运行。\n"
                "[backfill_tick] （外挂本身没有 ContextInfo，必须由策略代拉。）"
            )
            return 6
        print("[backfill_tick] 警告：心跳不新鲜，仍提交请求（--no-require-strategy）")
    else:
        print("[backfill_tick] 大 QMT 策略心跳正常（%s）" % detail)

    since = time.time()
    payload = submit_strategy_backfill(planned, force=force, limit=limit, merge=True)
    print(
        "[backfill_tick] 已提交策略执行，用 ContextInfo 拉 tick：\n"
        "[backfill_tick]   文件: %s\n"
        "[backfill_tick]   队列: %s\n"
        "[backfill_tick]   force=%s limit=%s"
        % (
            _MANUAL_REQUEST,
            ",".join(payload.get("days") or []),
            payload.get("force"),
            payload.get("limit"),
        )
    )

    first = planned[0]
    if not wait_strategy_pickup(first, since, timeout_sec=pickup_timeout):
        return 7

    ok_days = 0
    for i, day_s in enumerate(planned, 1):
        print("=" * 60)
        print(
            "[backfill_tick] (%d/%d) 等待策略完成 %s  当前 parquet≈%d"
            % (i, len(planned), day_s, _parquet_count(day_s))
        )
        # 若队列尚未轮到该日，先等到出现进度或出队
        t_wait = time.time()
        while not _day_done(runner, day_s) and not _day_started_by_strategy(day_s, since):
            req_days = _normalize_days_field(_load_manual_request())
            if day_s in req_days and req_days and req_days[0] != day_s:
                print(
                    "[%s] 队列中，当前队首=%s…"
                    % (datetime.now().strftime("%H:%M:%S"), req_days[0])
                )
            if time.time() - t_wait > float(day_timeout):
                print("[backfill_tick] %s 一直未开跑，超时" % day_s)
                break
            time.sleep(max(5, poll_interval))
            # 若前序日已完成，放宽 since 以便检测本日
            if i > 1 and _day_done(runner, planned[i - 2]):
                since = min(since, time.time() - 5)

        if _day_done(runner, day_s):
            ok_days += 1
            continue
        if wait_day_done(
            runner,
            day_s,
            timeout_sec=day_timeout,
            interval_sec=poll_interval,
        ):
            ok_days += 1
        else:
            print("[backfill_tick] %s 未达完成标准，继续盯后续日" % day_s)

    print(
        "[backfill_tick] 全部结束：计划 %d 天，达标 %d 天（策略 ContextInfo 路径）"
        % (len(planned), ok_days)
    )
    return 0 if ok_days == len(planned) else 3


def _run_via_xtdata(
    runner,
    tick_io,
    xtdata,
    planned: List[str],
    *,
    force: bool,
    limit: int,
) -> int:
    tick_io.ENABLE_XTDATA_TICK_DOWNLOAD = True
    if hasattr(runner, "ENABLE_XTDATA_TICK_DOWNLOAD"):
        runner.ENABLE_XTDATA_TICK_DOWNLOAD = True
    print(
        "[backfill_tick] --allow-xtdata-download：外挂直连 xtdata（需 miniQMT），"
        "非默认 ContextInfo 路径"
    )
    ok_days = 0
    for i, day_s in enumerate(planned, 1):
        print("=" * 60)
        print(
            "[backfill_tick] (%d/%d) 外挂补 %s  parquet≈%d"
            % (i, len(planned), day_s, _parquet_count(day_s))
        )
        t0 = time.time()
        try:
            ok = runner.run_tick_full_sync(
                None,
                day=day_s,
                force=bool(force),
                limit=int(limit or 0),
            )
        except Exception as e:
            print("[backfill_tick] %s 异常: %s: %s" % (day_s, type(e).__name__, e))
            ok = False
        elapsed = (time.time() - t0) / 60.0
        n = _parquet_count(day_s)
        print(
            "[backfill_tick] %s 结束 ok=%s 耗时=%.1fmin parquet≈%d"
            % (day_s, ok, elapsed, n)
        )
        if ok or _day_done(runner, day_s):
            ok_days += 1
    print(
        "[backfill_tick] 全部结束：计划 %d 天，达标 %d 天（xtdata 路径）"
        % (len(planned), ok_days)
    )
    return 0 if ok_days == len(planned) else 3


def main() -> int:
    ap = argparse.ArgumentParser(
        description="经大 QMT 策略 ContextInfo 补全历史全A tick（约1个月窗口）"
    )
    ap.add_argument(
        "--lookback",
        type=int,
        default=28,
        help="自然日回看天数（默认 28，对齐券商 tick 留存；更早的日会被跳过）",
    )
    ap.add_argument("--from", dest="from_s", default="", help="起始日 YYYYMMDD")
    ap.add_argument("--to", dest="to_s", default="", help="结束日 YYYYMMDD（默认昨天）")
    ap.add_argument(
        "--max-days",
        type=int,
        default=2,
        help="本次最多补几个交易日；0=不限制。默认 2（取最近缺失日）",
    )
    ap.add_argument(
        "--oldest-first",
        action="store_true",
        help="从最早缺失日开始（默认改为最近缺失日，更贴 QMT 窗口）",
    )
    ap.add_argument(
        "--wait-today",
        action="store_true",
        help="若今日官方 tick 正在跑，则等到完成/停住再开始",
    )
    ap.add_argument(
        "--skip-wait",
        action="store_true",
        help="不等今日 tick（即使正在跑也开补；可能抢行情）",
    )
    ap.add_argument(
        "--wait-timeout",
        type=int,
        default=21600,
        help="等今日完成的超时秒数（默认 6h）",
    )
    ap.add_argument(
        "--include-today",
        action="store_true",
        help="允许补今天（一般不需要）",
    )
    ap.add_argument("--dry-run", action="store_true", help="只列出将要补的日期，不写请求")
    ap.add_argument(
        "--force",
        action="store_true",
        help="force=true 传给策略（已有文件也重拉）",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=0,
        help="每只日只同步前 N 只股票（试跑用；0=全A）",
    )
    ap.add_argument(
        "--pickup-timeout",
        type=int,
        default=120,
        help="提交后等待策略领取的秒数（默认 120）",
    )
    ap.add_argument(
        "--day-timeout",
        type=int,
        default=14400,
        help="单日等待完成超时秒数（默认 4h）",
    )
    ap.add_argument(
        "--poll-interval",
        type=int,
        default=30,
        help="进度轮询间隔秒（默认 30）",
    )
    ap.add_argument(
        "--no-require-strategy",
        action="store_true",
        help="心跳不新鲜仍提交 manual_request（不推荐）",
    )
    ap.add_argument(
        "--submit-only",
        action="store_true",
        help="只写 manual_request.json 后退出（不轮询）",
    )
    ap.add_argument(
        "--allow-xtdata-download",
        action="store_true",
        help="非默认：外挂直连 miniQMT/xtdata.download（违背大QMT-only）",
    )
    args = ap.parse_args()

    if args.wait_today or args.skip_wait:
        if not wait_today_tick_done(
            args.wait_timeout,
            skip_wait=bool(args.skip_wait),
        ):
            return 1

    runner = _import_runner()
    tick_io = _import_tick_io()
    xtdata = None
    try:
        xtdata = runner._load_xtdata()
    except Exception:
        xtdata = None

    if xtdata is not None:
        alive, alive_detail = _check_xtdata_alive(tick_io, xtdata)
        if not alive:
            print(
                "[backfill_tick] 外挂 xtdata 不可用（%s）；"
                "交易日日历改用工作日回退。策略 ContextInfo 路径不依赖此外挂连接。"
                % alive_detail
            )
            xtdata = None
    else:
        print(
            "[backfill_tick] 无 xtquant.xtdata；交易日按工作日估算。"
            "正式补数走策略 ContextInfo。"
        )

    from_d = _parse_ymd(args.from_s) if args.from_s else None
    to_d = _parse_ymd(args.to_s) if args.to_s else None
    candidates = list_candidate_days(
        xtdata,
        runner,
        lookback_calendar=int(args.lookback),
        from_d=from_d,
        to_d=to_d,
        include_today=bool(args.include_today),
    )
    print(
        "[backfill_tick] 候选缺失日 %d 个（从早到晚）: %s"
        % (len(candidates), ",".join(candidates) if candidates else "(无)")
    )
    if not candidates:
        print("[backfill_tick] 无需补数")
        return 0

    # 默认取「最近」缺失日（更可能仍在大 QMT ~1 个月窗口内）。
    # --oldest-first：从最早缺失日开始（旧行为；窗外日易被策略 ctx-empty 中止）。
    planned = list(candidates)
    truncated = False
    if args.max_days and args.max_days > 0:
        n = int(args.max_days)
        if len(planned) > n:
            truncated = True
        if args.oldest_first:
            planned = planned[:n]
        else:
            planned = planned[-n:]
    elif not args.oldest_first:
        # 不限制天数时仍从早到晚整段提交；窗口外由策略侧 abort-hold 兜底
        pass

    if truncated:
        print(
            "[backfill_tick] 注意：--max-days %d 从 %d 个候选中只取 %s %d 天 → %s"
            % (
                int(args.max_days),
                len(candidates),
                "最早" if args.oldest_first else "最近",
                len(planned),
                ",".join(planned) if planned else "(无)",
            )
        )
        print(
            "[backfill_tick] 若要全部入队：加 --max-days 0"
            "（可加 --from/--to 限定区间；勿开 --allow-xtdata-download）"
        )

    if args.dry_run:
        print(
            "[backfill_tick] dry-run：将提交策略 ContextInfo 队列（不写文件）: %s"
            % (",".join(planned) if planned else "(无)")
        )
        from_part = (" --from %s" % args.from_s) if args.from_s else ""
        to_part = (" --to %s" % args.to_s) if args.to_s else ""
        print(
            "[backfill_tick] 实际命令示例:\n"
            "  python tools/backfill_tick_history.py --wait-today --max-days %d%s%s --submit-only\n"
            "  # 全量候选入队: --max-days 0"
            % (
                args.max_days if args.max_days > 0 else 0,
                from_part,
                to_part,
            )
        )
        ver = getattr(runner, "TICK_FULL_SYNC_VERSION", "?")
        print("[backfill_tick] runner version=%s request=%s" % (ver, _MANUAL_REQUEST))
        return 0

    print("[backfill_tick] 本次计划 %d 天: %s" % (len(planned), ",".join(planned)))

    if args.allow_xtdata_download:
        if xtdata is None:
            print("[backfill_tick] --allow-xtdata-download 需要可用的 xtdata/miniQMT")
            return 5
        return _run_via_xtdata(
            runner,
            tick_io,
            xtdata,
            planned,
            force=bool(args.force),
            limit=int(args.limit or 0),
        )

    if args.submit_only:
        alive, detail = _strategy_alive()
        print("[backfill_tick] 策略心跳: alive=%s (%s)" % (alive, detail))
        if not alive and not args.no_require_strategy:
            print("[backfill_tick] 策略未在跑，中止提交（或加 --no-require-strategy）")
            return 6
        payload = submit_strategy_backfill(
            planned, force=bool(args.force), limit=int(args.limit or 0), merge=True
        )
        print(
            "[backfill_tick] 已提交策略执行，用 ContextInfo 拉…\n"
            "[backfill_tick]   %s\n"
            "[backfill_tick]   days=%s"
            % (_MANUAL_REQUEST, ",".join(payload.get("days") or []))
        )
        return 0

    return _run_via_strategy(
        runner,
        planned,
        force=bool(args.force),
        limit=int(args.limit or 0),
        pickup_timeout=int(args.pickup_timeout),
        day_timeout=int(args.day_timeout),
        poll_interval=int(args.poll_interval),
        require_alive=not bool(args.no_require_strategy),
    )


if __name__ == "__main__":
    raise SystemExit(main())
