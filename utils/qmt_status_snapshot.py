# -*- coding: utf-8 -*-
"""
大 QMT 状态只读快照：聚合队列 / 进度 / 在线 / 告警 / 粗算 ETA。
不写盘、不向 QMT 发指令。可供 GUI 与命令行复用。
"""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from datetime import datetime, time as dt_time, timedelta
from typing import Any, Dict, List, Optional, Tuple

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(_PROJECT_ROOT, "data")
RESULTS_PATH = os.path.join(DATA, "results.json")
RULES_ARMED_PATH = os.path.join(DATA, "rules_armed.json")
SYNC_REQ_PATH = os.path.join(DATA, "data_sync_requests.json")
TICK_FULL_DIR = os.path.join(DATA, "tick_full_sync")
MANUAL_TICK_REQ = os.path.join(TICK_FULL_DIR, "manual_request.json")
TICK_PAUSE = os.path.join(TICK_FULL_DIR, "PAUSE")
TICKS_DIR = os.path.join(DATA, "ticks")
DAILY_CACHE_DIR = os.path.join(DATA, "daily_cache")
DAILY_MANIFEST = os.path.join(DAILY_CACHE_DIR, "manifest.json")
FORCE_YEAR = os.path.join(DAILY_CACHE_DIR, "FORCE_YEAR_BACKFILL")
RESET_FORCE_PROGRESS = os.path.join(DAILY_CACHE_DIR, "RESET_FORCE_PROGRESS")
DEFAULT_BACKFILL_START = "20250101"  # 与 ant_daily_sync_runner.BACKFILL_START_YMD 对齐
AFTER_RANK_DIR = os.path.join(DATA, "after_hours_rank")
AFTER_RANK_REQ = os.path.join(AFTER_RANK_DIR, "manual_request.json")
AFTER_RANK_MIN_DETAIL_ROWS = 2000  # 与 ant_after_hours_rank_runner._MIN_DETAIL_ROWS 对齐
LAUNCHER_LOG = os.path.join(_PROJECT_ROOT, "logs", "launcher_run.log")

# manifest / 队列 status 展示用
_STATUS_CN = {
    "running": "运行中",
    "paused": "已暂停",
    "incomplete": "未完成",
    "completed": "已完成",
    "failed": "失败",
    "pending": "待处理",
    "unknown": "未知",
}


def status_cn(raw: Any) -> str:
    """将 runner 内部 status 译为中文；未知原样返回。"""
    s = str(raw or "").strip()
    if not s:
        return "—"
    return _STATUS_CN.get(s, s)


def _normalize_ymd(raw: Any) -> Optional[str]:
    s = str(raw or "").strip().replace("-", "").replace("/", "")
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        datetime.strptime(s, "%Y%m%d")
    except ValueError:
        return None
    return s


def parse_force_backfill_start(path: str = FORCE_YEAR) -> str:
    """读 FORCE 文件内 start；空/无效则返回默认 DEFAULT_BACKFILL_START。"""
    if not os.path.isfile(path):
        return DEFAULT_BACKFILL_START
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = (f.read() or "").strip()
    except Exception:
        return DEFAULT_BACKFILL_START
    if not text:
        return DEFAULT_BACKFILL_START
    if text.startswith("{"):
        try:
            obj = json.loads(text)
        except Exception:
            return DEFAULT_BACKFILL_START
        if isinstance(obj, dict):
            ymd = _normalize_ymd(obj.get("start") or obj.get("backfill_start"))
            return ymd or DEFAULT_BACKFILL_START
        return DEFAULT_BACKFILL_START
    return _normalize_ymd(text) or DEFAULT_BACKFILL_START


def force_start_text(ymd: Optional[str] = None) -> str:
    """YYYYMMDD → YYYY-MM-DD 展示。"""
    s = _normalize_ymd(ymd) or DEFAULT_BACKFILL_START
    return "%s-%s-%s" % (s[:4], s[4:6], s[6:8])

# 与 qmt_builtin runners 对齐的常量（监控器侧硬编码，避免导入 QMT 模块）
SYNC_HOUR = 15
SYNC_MINUTE = 35
TICK_CHAIN_DELAY_SEC = 30
MARKET_PROTECT_START = dt_time(9, 0)
MARKET_PROTECT_END = dt_time(15, 30)
RESULTS_STALE_SEC = 180.0
# 与 builtin_price_feed 对齐：多只 last_tick_time 中位落后则告警
QUOTE_LAG_ALERT_SEC = 55.0
QUOTE_LAG_SAMPLE_MIN = 3
MAX_RETRIES = 3

# 粗算默认吞吐（秒/项）——无近期样本时使用
DEFAULT_SEC_PER_ONDEMAND_TICK = 40.0
DEFAULT_SEC_PER_ONDEMAND_DAILY = 8.0
DEFAULT_SEC_PER_FULL_TICK = 0.35  # 约 140 只/分钟 → ~0.43s；略保守

# 进程内滑动样本：(ts, done_count) for full tick progress rate
_progress_samples: Dict[str, List[Tuple[float, int]]] = {}
_last_activity_hint: str = ""


def _read_json(path: str) -> Optional[Any]:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _mtime(path: str) -> Optional[float]:
    try:
        return os.path.getmtime(path) if os.path.isfile(path) else None
    except OSError:
        return None


def _parse_iso_ts(raw: Any) -> Optional[float]:
    s = str(raw or "").strip()
    if not s:
        return None
    s19 = s[:19]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s19, fmt).timestamp()
        except ValueError:
            continue
    return None


def _fmt_eta(seconds: Optional[float]) -> str:
    if seconds is None or seconds < 0:
        return "样本不足"
    if seconds < 60:
        return "约 %d 秒" % int(seconds)
    if seconds < 3600:
        return "约 %d 分钟" % max(1, int(round(seconds / 60.0)))
    h = int(seconds // 3600)
    m = int(round((seconds % 3600) / 60.0))
    return "约 %d 小时 %d 分钟" % (h, m)


def _is_weekday(now: Optional[datetime] = None) -> bool:
    n = now or datetime.now()
    return n.weekday() < 5


def _in_market_protect(now: Optional[datetime] = None) -> bool:
    n = now or datetime.now()
    if not _is_weekday(n):
        return False
    t = n.time()
    return MARKET_PROTECT_START <= t <= MARKET_PROTECT_END


def _collect_ondemand() -> Dict[str, Any]:
    data = _read_json(SYNC_REQ_PATH) or {}
    daily = data.get("daily") or {}
    tick_root = data.get("tick") or {}
    daily_pending = 0
    daily_failed = 0
    tick_pending = 0
    tick_failed = 0
    tick_by_day: Counter = Counter()
    if isinstance(daily, dict):
        for meta in daily.values():
            if not isinstance(meta, dict):
                continue
            st = str(meta.get("status") or "")
            retries = int(meta.get("retries") or 0)
            if st == "pending" and retries < MAX_RETRIES:
                daily_pending += 1
            elif st == "failed" or (st == "pending" and retries >= MAX_RETRIES):
                daily_failed += 1
    if isinstance(tick_root, dict):
        for days in tick_root.values():
            if not isinstance(days, dict):
                continue
            for ymd, meta in days.items():
                if not isinstance(meta, dict):
                    continue
                st = str(meta.get("status") or "")
                retries = int(meta.get("retries") or 0)
                if st == "pending" and retries < MAX_RETRIES:
                    tick_pending += 1
                    tick_by_day[str(ymd)] += 1
                elif st == "failed" or (st == "pending" and retries >= MAX_RETRIES):
                    tick_failed += 1
    by_day = [{"day": k, "pending": int(v)} for k, v in sorted(tick_by_day.items())]
    return {
        "daily_pending": daily_pending,
        "tick_pending": tick_pending,
        "daily_failed": daily_failed,
        "tick_failed": tick_failed,
        "tick_by_day": by_day,
        "path": SYNC_REQ_PATH,
        "mtime": _mtime(SYNC_REQ_PATH),
    }


def _collect_tick_full() -> Dict[str, Any]:
    pause = os.path.isfile(TICK_PAUSE)
    manual = _read_json(MANUAL_TICK_REQ) or {}
    days: List[str] = []
    if isinstance(manual, dict):
        raw = manual.get("days") or manual.get("day")
        if isinstance(raw, list):
            days = [str(x) for x in raw if str(x).strip()]
        elif raw:
            days = [str(raw).strip()]
    # 找最近有 progress / 正在跑的日
    active_day = days[0] if days else ""
    progress = None
    done_marker = None
    rate_sec = None
    remain = None
    eta_sec = None
    if not active_day and os.path.isdir(TICKS_DIR):
        # 取 mtime 最新的 progress
        newest = ("", 0.0)
        try:
            for name in os.listdir(TICKS_DIR):
                if not (name.isdigit() and len(name) == 8):
                    continue
                pp = os.path.join(TICKS_DIR, name, "_full_sync_progress.json")
                mt = _mtime(pp)
                if mt and mt > newest[1]:
                    newest = (name, mt)
        except OSError:
            pass
        # 仅当 progress 在 10 分钟内更新才视为 active
        if newest[0] and (time.time() - newest[1]) < 600:
            active_day = newest[0]

    if active_day:
        pp = os.path.join(TICKS_DIR, active_day, "_full_sync_progress.json")
        dp = os.path.join(TICKS_DIR, active_day, "_full_sync_done.json")
        progress = _read_json(pp)
        done_marker = _read_json(dp)
        if isinstance(progress, dict):
            done_n = len(progress.get("done") or [])
            fail_n = len(progress.get("fail") or {})
            updated = _parse_iso_ts(progress.get("updated_at")) or _mtime(pp) or time.time()
            key = "full:%s" % active_day
            samples = _progress_samples.setdefault(key, [])
            samples.append((time.time(), done_n))
            # 保留 15 分钟内样本
            cutoff = time.time() - 900
            samples[:] = [s for s in samples if s[0] >= cutoff]
            if len(samples) >= 2:
                dt = samples[-1][0] - samples[0][0]
                dd = samples[-1][1] - samples[0][1]
                if dt > 5 and dd > 0:
                    rate_sec = dt / float(dd)
            total_guess = 5206
            if isinstance(done_marker, dict) and done_marker.get("total"):
                try:
                    total_guess = int(done_marker.get("total") or total_guess)
                except (TypeError, ValueError):
                    pass
            remain = max(0, total_guess - done_n - fail_n)
            sec_per = rate_sec if rate_sec else DEFAULT_SEC_PER_FULL_TICK
            # 盘中保护时有效吞吐更低：工作片约 60s，空档约 10s → 粗算 ×1.3
            if _in_market_protect():
                sec_per *= 1.5
            eta_sec = remain * sec_per if remain is not None else None

    # 最近一次「已完成」的盘后分时（按 finished_at 取最新）
    last_sync_day = ""
    last_finished_at = ""
    last_started_at = ""
    if os.path.isdir(TICKS_DIR):
        best_ts = 0.0
        try:
            for name in os.listdir(TICKS_DIR):
                if not (name.isdigit() and len(name) == 8):
                    continue
                dp = os.path.join(TICKS_DIR, name, "_full_sync_done.json")
                marker = _read_json(dp)
                if not isinstance(marker, dict):
                    continue
                fin = str(marker.get("finished_at") or "").strip()
                ts = _parse_iso_ts(fin) or _mtime(dp) or 0.0
                if ts <= best_ts:
                    continue
                best_ts = float(ts)
                last_sync_day = str(marker.get("day") or name)
                last_finished_at = fin or (
                    datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                    if ts
                    else ""
                )
                last_started_at = str(marker.get("started_at") or "").strip()
        except OSError:
            pass

    last_completed_text = "—"
    if last_finished_at:
        day_show = last_sync_day
        if len(day_show) == 8 and day_show.isdigit():
            day_show = "%s-%s-%s" % (day_show[:4], day_show[4:6], day_show[6:8])
        if day_show and day_show not in last_finished_at:
            last_completed_text = "%s（交易日 %s）" % (last_finished_at, day_show)
        else:
            last_completed_text = last_finished_at
    elif last_sync_day:
        day_show = last_sync_day
        if len(day_show) == 8 and day_show.isdigit():
            day_show = "%s-%s-%s" % (day_show[:4], day_show[4:6], day_show[6:8])
        last_completed_text = "交易日 %s" % day_show

    return {
        "pause": pause,
        "manual_days": days,
        "manual_force": bool(manual.get("force")) if isinstance(manual, dict) else False,
        "active_day": active_day,
        "progress": {
            "done": len((progress or {}).get("done") or []) if isinstance(progress, dict) else 0,
            "fail": len((progress or {}).get("fail") or {}) if isinstance(progress, dict) else 0,
            "updated_at": (progress or {}).get("updated_at") if isinstance(progress, dict) else None,
        }
        if progress
        else None,
        "done_marker": bool(done_marker),
        "remain_est": remain,
        "sec_per_code": rate_sec,
        "eta_sec": eta_sec,
        "eta_text": _fmt_eta(eta_sec) if (remain and remain > 0) else ("—" if not days and not progress else _fmt_eta(eta_sec)),
        "last_sync_day": last_sync_day,
        "last_started_at": last_started_at,
        "last_finished_at": last_finished_at,
        "last_completed_text": last_completed_text,
    }


def _collect_daily_manifest() -> Dict[str, Any]:
    man = _read_json(DAILY_MANIFEST) or {}
    force = os.path.isfile(FORCE_YEAR)
    force_start = parse_force_backfill_start(FORCE_YEAR)
    status = str(man.get("status") or "") if isinstance(man, dict) else ""
    progress = man.get("progress") if isinstance(man, dict) else None
    universe = int(man.get("universe_count") or 0) if isinstance(man, dict) else 0
    ok = int(man.get("ok_count") or 0) if isinstance(man, dict) else 0
    fail = int(man.get("fail_count") or 0) if isinstance(man, dict) else 0
    sync_date = str(man.get("sync_trade_date") or "") if isinstance(man, dict) else ""
    finished_at = str(man.get("finished_at") or "").strip() if isinstance(man, dict) else ""
    started_at = str(man.get("started_at") or "").strip() if isinstance(man, dict) else ""
    # 展示友好：2026-08-03T15:37:37 → 2026-08-03 15:37:37
    finished_show = finished_at.replace("T", " ") if finished_at else ""
    started_show = started_at.replace("T", " ") if started_at else ""
    last_completed_text = "—"
    if finished_show:
        if sync_date and sync_date not in finished_show:
            last_completed_text = "%s（交易日 %s）" % (finished_show, sync_date)
        else:
            last_completed_text = finished_show
    elif sync_date:
        last_completed_text = "交易日 %s" % sync_date
    eta_sec = None
    if status == "running" and universe > 0 and isinstance(progress, (int, float)):
        remain = max(0, universe - int(progress))
        eta_sec = remain * 0.15  # 粗算：软切片日线很快，仅示意
    return {
        "status": status or "unknown",
        "status_cn": status_cn(status or "unknown"),
        "progress": progress,
        "universe_count": universe,
        "ok_count": ok,
        "fail_count": fail,
        "sync_trade_date": sync_date,
        "started_at": started_show,
        "finished_at": finished_show,
        "last_completed_text": last_completed_text,
        "force_year": force,
        "force_start": force_start,
        "force_start_text": force_start_text(force_start),
        "eta_text": _fmt_eta(eta_sec) if status == "running" else "—",
    }


def _after_rank_detail_ready(detail_path: str) -> bool:
    """detail.csv 足量行则视为当日量能已完成（与 runner._detail_ready 一致）。"""
    if not os.path.isfile(detail_path):
        return False
    try:
        n = 0
        with open(detail_path, "r", encoding="utf-8-sig") as f:
            for i, _line in enumerate(f):
                if i == 0:
                    continue
                n += 1
                if n >= AFTER_RANK_MIN_DETAIL_ROWS:
                    return True
        return n >= AFTER_RANK_MIN_DETAIL_ROWS
    except Exception:
        return False


def _after_rank_finished_at(day: str, detail_path: str) -> str:
    """优先从 YYYYMMDD_run.log 末条「完成」行取时刻，日期用 detail.csv mtime。"""
    mt = _mtime(detail_path)
    base_dt = datetime.fromtimestamp(mt) if mt else None
    log_path = os.path.join(AFTER_RANK_DIR, "%s_run.log" % day)
    finish_hms = ""
    if os.path.isfile(log_path):
        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    s = line.strip()
                    if not s:
                        continue
                    # "17:13:34 完成 耗时=563s ..."
                    parts = s.split(None, 1)
                    if (
                        len(parts) == 2
                        and parts[1].startswith("完成")
                        and len(parts[0]) == 8
                        and parts[0][2] == ":"
                    ):
                        finish_hms = parts[0]
        except Exception:
            pass
    if base_dt and finish_hms:
        try:
            h, m, sec = [int(x) for x in finish_hms.split(":")]
            return base_dt.replace(
                hour=h, minute=m, second=sec, microsecond=0
            ).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            pass
    if base_dt:
        return base_dt.strftime("%Y-%m-%d %H:%M:%S")
    return ""


def _collect_after_rank() -> Dict[str, Any]:
    req = _read_json(AFTER_RANK_REQ)
    pending = False
    day = ""
    force = False
    if isinstance(req, dict):
        day = str(req.get("day") or "").strip()
        if not day:
            days = req.get("days")
            if isinstance(days, list) and days:
                day = str(days[0] or "").strip()
        pending = bool(day)
        force = bool(req.get("force"))

    # 最近一次已完成的盘后量能（足量 detail.csv，按完成时间取最新）
    last_sync_day = ""
    last_finished_at = ""
    best_ts = 0.0
    if os.path.isdir(AFTER_RANK_DIR):
        try:
            for name in os.listdir(AFTER_RANK_DIR):
                if not (name.isdigit() and len(name) == 8):
                    continue
                detail = os.path.join(AFTER_RANK_DIR, name, "detail.csv")
                if not _after_rank_detail_ready(detail):
                    continue
                fin = _after_rank_finished_at(name, detail)
                ts = _parse_iso_ts(fin) or _mtime(detail) or 0.0
                if ts <= best_ts:
                    continue
                best_ts = float(ts)
                last_sync_day = name
                last_finished_at = fin or (
                    datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                    if ts
                    else ""
                )
        except OSError:
            pass

    last_completed_text = "—"
    if last_finished_at:
        day_show = last_sync_day
        if len(day_show) == 8 and day_show.isdigit():
            day_show = "%s-%s-%s" % (day_show[:4], day_show[4:6], day_show[6:8])
        if day_show and day_show not in last_finished_at:
            last_completed_text = "%s（交易日 %s）" % (last_finished_at, day_show)
        else:
            last_completed_text = last_finished_at
    elif last_sync_day:
        day_show = last_sync_day
        if len(day_show) == 8 and day_show.isdigit():
            day_show = "%s-%s-%s" % (day_show[:4], day_show[4:6], day_show[6:8])
        last_completed_text = "交易日 %s" % day_show

    return {
        "pending": pending,
        "day": day,
        "force": force,
        "last_sync_day": last_sync_day,
        "last_finished_at": last_finished_at,
        "last_completed_text": last_completed_text,
    }


def _alert(aid: str, text: str) -> Dict[str, str]:
    """结构化告警：id 跨轮询稳定，text 可含动态数值。"""
    return {"id": str(aid), "text": str(text)}


def _alert_text(a: Any) -> str:
    if isinstance(a, dict):
        return str(a.get("text") or a.get("message") or "")
    return str(a or "")


def _tick_time_lag_sec(tick_hhmmss: Any, now: Optional[datetime] = None) -> Optional[float]:
    s = str(tick_hhmmss or "").strip()
    if len(s) < 8:
        return None
    now = now or datetime.now()
    try:
        parts = s[:8].split(":")
        t = now.replace(
            hour=int(parts[0]),
            minute=int(parts[1]),
            second=int(float(parts[2])),
            microsecond=0,
        )
        lag = (now - t).total_seconds()
        if lag < -120:
            return None
        return max(0.0, lag)
    except Exception:
        return None


def _in_quote_watch_window(now: Optional[datetime] = None) -> bool:
    """连续竞价时段才盯行情推送；开盘/午后开盘后约 90 秒宽限。

    午休 11:30–13:00 无推送是正常的。若 13:00 整点立刻用「距上次推送」判滞后，
    会把午休 90 分钟当成故障闪一下告警（已解除），故与 builtin_price_feed 对齐做宽限。
    """
    now = now or datetime.now()
    t = now.time()
    in_morning = dt_time(9, 30) <= t <= dt_time(11, 30)
    in_afternoon = dt_time(13, 0) <= t <= dt_time(15, 0)
    if not (in_morning or in_afternoon):
        return False
    # 9:30 / 13:00 开段后约 90 秒：订阅与首笔推送尚未到达属正常
    if dt_time(9, 30) <= t < dt_time(9, 31, 30):
        return False
    if dt_time(13, 0) <= t < dt_time(13, 1, 30):
        return False
    return True


def _quote_recv_lag_sec(recv_dt: datetime, now: Optional[datetime] = None) -> float:
    """推送滞后秒数；跨过午休时扣掉 11:30–13:00，避免把休市算进故障时长。"""
    now = now or datetime.now()
    age = max(0.0, (now - recv_dt).total_seconds())
    if now.date() != recv_dt.date():
        return age
    lunch_start = datetime.combine(now.date(), dt_time(11, 30))
    lunch_end = datetime.combine(now.date(), dt_time(13, 0))
    lunch_sec = (lunch_end - lunch_start).total_seconds()
    if recv_dt <= lunch_start and now >= lunch_end:
        age = max(0.0, age - lunch_sec)
    elif lunch_start < recv_dt < lunch_end and now >= lunch_end:
        age = max(0.0, (now - lunch_end).total_seconds())
    return age


def _fmt_bytes(n: Optional[int]) -> str:
    if n is None or n < 0:
        return "—"
    try:
        n = int(n)
    except (TypeError, ValueError):
        return "—"
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.1f KB" % (n / 1024.0)
    return "%.2f MB" % (n / (1024.0 * 1024.0))


def _file_size(path: str) -> Optional[int]:
    try:
        if os.path.isfile(path):
            return int(os.path.getsize(path))
    except OSError:
        pass
    return None


def _collect_results() -> Dict[str, Any]:
    mt = _mtime(RESULTS_PATH)
    age = (time.time() - mt) if mt else None
    online = bool(age is not None and age < RESULTS_STALE_SEC)
    size = _file_size(RESULTS_PATH)
    data = _read_json(RESULTS_PATH) or {}
    account = data.get("account") if isinstance(data, dict) else {}
    pos_q = data.get("position_query") if isinstance(data, dict) else {}
    alerts: List[Dict[str, str]] = []
    if not online:
        if age is None:
            alerts.append(_alert("results_missing", "results.json 不存在：策略可能未运行"))
        else:
            alerts.append(
                _alert(
                    "heartbeat_timeout",
                    "策略心跳超时（%.0fs）：大 QMT 可能离线或卡住" % age,
                )
            )
    if isinstance(pos_q, dict) and pos_q.get("pos_raw_is_none"):
        alerts.append(_alert("pos_raw_none", "持仓查询返回空（pos_raw_is_none）"))

    stocks = data.get("stocks") if isinstance(data, dict) and isinstance(data.get("stocks"), dict) else {}
    positions = (
        data.get("positions")
        if isinstance(data, dict) and isinstance(data.get("positions"), dict)
        else {}
    )
    orders = data.get("orders") if isinstance(data, dict) and isinstance(data.get("orders"), list) else []
    broker_orders = (
        data.get("broker_orders")
        if isinstance(data, dict) and isinstance(data.get("broker_orders"), list)
        else []
    )
    done_ids = (
        data.get("done_task_ids")
        if isinstance(data, dict) and isinstance(data.get("done_task_ids"), list)
        else []
    )

    # 文件心跳活着 ≠ 行情推送活着：账户快照也会刷新 updated_at
    quote_lag_sec = None
    quotes_recv_at = ""
    if online and isinstance(data, dict):
        quotes_recv_at = str(data.get("quotes_recv_at") or "").strip()
    if online and _in_quote_watch_window() and isinstance(data, dict):
        now = datetime.now()
        recv_raw = quotes_recv_at
        if not recv_raw:
            for _code, snap in stocks.items():
                if not isinstance(snap, dict):
                    continue
                recv_raw = str(snap.get("quote_recv_at") or "").strip()
                if recv_raw:
                    break
        recv_age = None
        if recv_raw:
            try:
                if "T" in recv_raw:
                    dt = datetime.strptime(recv_raw[:19], "%Y-%m-%dT%H:%M:%S")
                elif " " in recv_raw:
                    dt = datetime.strptime(recv_raw[:19], "%Y-%m-%d %H:%M:%S")
                else:
                    dt = None
                if dt is not None:
                    recv_age = _quote_recv_lag_sec(dt, now)
            except Exception:
                recv_age = None
        if recv_age is not None and recv_age >= float(QUOTE_LAG_ALERT_SEC):
            quote_lag_sec = float(recv_age)
            alerts.append(
                _alert(
                    "quote_lag",
                    "行情推送停约%.0f秒（results 心跳仍在；请查订阅/补种）"
                    % recv_age,
                )
            )

    return {
        "path": RESULTS_PATH,
        "exists": bool(mt),
        "size_bytes": size,
        "size_text": _fmt_bytes(size),
        "online": online,
        "age_sec": age,
        "quote_lag_sec": quote_lag_sec,
        "quotes_recv_at": quotes_recv_at,
        "updated_at": (data.get("updated_at") if isinstance(data, dict) else None) or "",
        "mode": (data.get("mode") if isinstance(data, dict) else None) or "",
        "trade_date": (data.get("trade_date") if isinstance(data, dict) else None) or "",
        "n_stocks": len(stocks),
        "n_positions": len(positions),
        "n_orders": len(orders),
        "n_broker_orders": len(broker_orders),
        "n_done_task_ids": len(done_ids),
        "total_asset": (account or {}).get("total_asset") if isinstance(account, dict) else None,
        "cash": (account or {}).get("cash") if isinstance(account, dict) else None,
        "alerts": alerts,
    }


def _collect_rules_armed() -> Dict[str, Any]:
    """主程序 → 大 QMT：规则/订阅清单（rules_armed.json）。"""
    mt = _mtime(RULES_ARMED_PATH)
    age = (time.time() - mt) if mt else None
    size = _file_size(RULES_ARMED_PATH)
    data = _read_json(RULES_ARMED_PATH) if mt else None
    if not isinstance(data, dict):
        data = {}
    tasks = data.get("tasks") if isinstance(data.get("tasks"), list) else []
    watch = data.get("watch_codes") if isinstance(data.get("watch_codes"), list) else []
    pool = (
        data.get("strategy_pool_watch")
        if isinstance(data.get("strategy_pool_watch"), list)
        else []
    )
    subscribe = (
        data.get("subscribe_codes")
        if isinstance(data.get("subscribe_codes"), list)
        else []
    )
    # 若未写 subscribe_codes，按 tasks∪watch∪pool 估算
    if not subscribe and (tasks or watch or pool):
        codes = set()
        for t in tasks:
            if isinstance(t, dict):
                c = str(t.get("stock_code") or "").strip()
                if c:
                    codes.add(c)
        for c in list(watch) + list(pool):
            s = str(c or "").strip()
            if s:
                codes.add(s)
        n_subscribe = len(codes)
    else:
        n_subscribe = len(subscribe)

    rule_types: Dict[str, int] = {}
    for t in tasks:
        if not isinstance(t, dict):
            continue
        rt = str(t.get("rule_type") or "").strip() or "?"
        rule_types[rt] = int(rule_types.get(rt) or 0) + 1
    type_bits = []
    for k in sorted(rule_types.keys()):
        type_bits.append("%s×%d" % (k, rule_types[k]))

    return {
        "path": RULES_ARMED_PATH,
        "exists": bool(mt),
        "size_bytes": size,
        "size_text": _fmt_bytes(size),
        "age_sec": age,
        "updated_at": str(data.get("updated_at") or ""),
        "trade_date": str(data.get("trade_date") or ""),
        "orders_enabled": data.get("orders_enabled"),
        "n_tasks": len(tasks),
        "n_watch": len(watch),
        "n_pool_watch": len(pool),
        "n_subscribe": n_subscribe,
        "rule_types_text": "、".join(type_bits) if type_bits else "—",
        "strategy_pool_watch_at": str(data.get("strategy_pool_watch_at") or ""),
    }


def _tail_activity_from_log(max_bytes: int = 120000) -> str:
    """从 launcher_run.log 尾部取最近一条带同步前缀的行（可选增强）。"""
    if not os.path.isfile(LAUNCHER_LOG):
        return ""
    try:
        size = os.path.getsize(LAUNCHER_LOG)
        with open(LAUNCHER_LOG, "rb") as f:
            f.seek(max(0, size - max_bytes))
            chunk = f.read().decode("utf-8", errors="ignore")
    except Exception:
        return ""
    prefixes = ("[按需同步]", "[分笔同步]", "[日线同步]", "[交易核心]", "[盘后排名]")
    last = ""
    for line in chunk.splitlines():
        if any(p in line for p in prefixes):
            last = line.strip()
    if not last:
        return ""
    # 截断
    if len(last) > 160:
        last = last[:157] + "..."
    return last


def _infer_activity(
    results: Dict[str, Any],
    ond: Dict[str, Any],
    tick_full: Dict[str, Any],
    daily: Dict[str, Any],
    after: Dict[str, Any],
    log_hint: str,
) -> str:
    global _last_activity_hint
    if not results.get("online"):
        hint = "策略离线 / 无心跳"
        _last_activity_hint = hint
        return hint
    if tick_full.get("pause") and tick_full.get("manual_days"):
        hint = "盘后分时已暂停（队列仍保留）"
        _last_activity_hint = hint
        return hint
    prog = tick_full.get("progress")
    if tick_full.get("active_day") and prog and (prog.get("done") or 0) >= 0:
        mt = _parse_iso_ts(prog.get("updated_at"))
        if mt and (time.time() - mt) < 120:
            hint = "盘后分时同步中 交易日=%s 进度≈%d" % (
                tick_full.get("active_day"),
                int(prog.get("done") or 0),
            )
            _last_activity_hint = hint
            return hint
    if daily.get("status") == "running":
        hint = "盘后日线同步中 交易日=%s 进度=%s" % (
            daily.get("sync_trade_date") or "?",
            daily.get("progress"),
        )
        _last_activity_hint = hint
        return hint
    if (ond.get("tick_pending") or 0) > 0 or (ond.get("daily_pending") or 0) > 0:
        hint = "按需同步排队中 日线=%d 分时=%d" % (
            int(ond.get("daily_pending") or 0),
            int(ond.get("tick_pending") or 0),
        )
        _last_activity_hint = hint
        return hint
    if tick_full.get("manual_days"):
        hint = "盘后分时手动队列待跑: %s" % ",".join(tick_full["manual_days"][:5])
        _last_activity_hint = hint
        return hint
    if after.get("pending"):
        hint = "盘后量能待跑 交易日=%s" % (after.get("day") or "?")
        _last_activity_hint = hint
        return hint
    if log_hint:
        # 取前缀
        for p in ("[按需同步]", "[分笔同步]", "[日线同步]", "[交易核心]", "[盘后排名]"):
            if p in log_hint:
                hint = "最近日志 " + p
                _last_activity_hint = hint
                return hint
    hint = "空闲（无活跃同步队列）"
    _last_activity_hint = hint
    return hint


def _busy_score(
    results: Dict[str, Any],
    ond: Dict[str, Any],
    tick_full: Dict[str, Any],
    daily: Dict[str, Any],
) -> Dict[str, Any]:
    """0–100 繁忙度粗分。"""
    score = 0
    if not results.get("online"):
        return {"score": 0, "label": "离线", "level": "offline"}
    if daily.get("status") == "running":
        score = max(score, 70)
    if tick_full.get("active_day") and tick_full.get("progress"):
        mt = _parse_iso_ts((tick_full.get("progress") or {}).get("updated_at"))
        if mt and time.time() - mt < 120:
            score = max(score, 80)
    pend = int(ond.get("tick_pending") or 0) + int(ond.get("daily_pending") or 0)
    if pend > 0:
        score = max(score, min(90, 30 + pend // 5))
    if tick_full.get("manual_days") and not tick_full.get("pause"):
        score = max(score, 55)
    if tick_full.get("pause"):
        score = max(score, 15)
    if score >= 70:
        level, label = "high", "繁忙"
    elif score >= 35:
        level, label = "mid", "有任务"
    else:
        level, label = "low", "轻载"
    return {"score": score, "label": label, "level": level}


def _schedule_block(now: Optional[datetime] = None) -> Dict[str, Any]:
    n = now or datetime.now()
    weekday = _is_weekday(n)
    protect = _in_market_protect(n)
    daily_at = n.replace(hour=SYNC_HOUR, minute=SYNC_MINUTE, second=0, microsecond=0)
    tick_chain_at = daily_at + timedelta(seconds=TICK_CHAIN_DELAY_SEC)
    items = [
        {
            "name": "全 A 日线同步",
            "when": "%02d:%02d（工作日）" % (SYNC_HOUR, SYNC_MINUTE),
            "note": "日线同步 runner（15:35 定时）",
        },
        {
            "name": "分笔全量（日线后链式）",
            "when": (
                "日线完成后约 +%d 秒（约 %s）"
                % (TICK_CHAIN_DELAY_SEC, tick_chain_at.strftime("%H:%M:%S"))
                if TICK_CHAIN_DELAY_SEC < 60
                else "日线完成后约 +%d 分钟（约 %s）"
                % (TICK_CHAIN_DELAY_SEC // 60, tick_chain_at.strftime("%H:%M"))
            ),
            "note": "链式延迟 %d 秒" % TICK_CHAIN_DELAY_SEC,
        },
        {
            "name": "盘中保护（分笔/部分手动暂缓）",
            "when": "%s–%s（仅工作日）"
            % (
                MARKET_PROTECT_START.strftime("%H:%M"),
                MARKET_PROTECT_END.strftime("%H:%M"),
            ),
            "note": "当前%s" % ("生效中" if protect else "未生效"),
        },
        {
            "name": "按需同步",
            "when": "策略心跳持续消费",
            "note": "盘中日线约1只/秒（拉至上一交易日）；请求队列 data_sync_requests.json",
        },
    ]
    return {
        "is_weekday": weekday,
        "in_market_protect": protect,
        "now": n.strftime("%Y-%m-%d %H:%M:%S"),
        "items": items,
        "today_daily_slot": daily_at.strftime("%H:%M"),
        "today_tick_chain_slot": tick_chain_at.strftime("%H:%M"),
    }


def build_snapshot() -> Dict[str, Any]:
    """构建完整状态快照（JSON 可序列化）。"""
    now = datetime.now()
    results = _collect_results()
    rules_armed = _collect_rules_armed()
    ond = _collect_ondemand()
    tick_full = _collect_tick_full()
    daily = _collect_daily_manifest()
    after = _collect_after_rank()
    log_hint = _tail_activity_from_log()
    activity = _infer_activity(results, ond, tick_full, daily, after, log_hint)
    busy = _busy_score(results, ond, tick_full, daily)
    schedule = _schedule_block(now)

    alerts: List[Dict[str, str]] = list(results.get("alerts") or [])
    if not rules_armed.get("exists"):
        alerts.append(
            _alert("rules_armed_missing", "rules_armed.json 不存在：主程序可能未同步规则")
        )
    elif int(rules_armed.get("n_pool_watch") or 0) >= 120:
        alerts.append(
            _alert(
                "pool_watch_large",
                "strategy_pool_watch=%d 只（临时订阅偏大，易拖垮行情）"
                % int(rules_armed.get("n_pool_watch") or 0),
            )
        )
    if tick_full.get("pause"):
        alerts.append(
            _alert(
                "tick_full_pause",
                "盘后分时已暂停：存在 data/tick_full_sync/PAUSE",
            )
        )
    if daily.get("status") in ("paused", "incomplete"):
        alerts.append(
            _alert(
                "daily_manifest_%s" % daily.get("status"),
                "日线清单状态=%s" % status_cn(daily.get("status")),
            )
        )
    if daily.get("force_year"):
        alerts.append(_alert("force_year_backfill", "回填标志存在（FORCE_YEAR_BACKFILL）"))
    if int(ond.get("tick_failed") or 0) >= 50:
        alerts.append(
            _alert(
                "ondemand_tick_failed",
                "按需分时失败堆积 %d" % int(ond["tick_failed"]),
            )
        )
    if int(ond.get("tick_pending") or 0) >= 50:
        alerts.append(
            _alert(
                "ondemand_tick_pending",
                "按需分时待处理较多：%d（粗算预计剩余见下）" % int(ond["tick_pending"]),
            )
        )

    # 按需 ETA
    od_tick_eta = None
    od_daily_eta = None
    if int(ond.get("tick_pending") or 0) > 0:
        od_tick_eta = int(ond["tick_pending"]) * DEFAULT_SEC_PER_ONDEMAND_TICK
    if int(ond.get("daily_pending") or 0) > 0:
        od_daily_eta = int(ond.get("daily_pending") or 0) * DEFAULT_SEC_PER_ONDEMAND_DAILY

    return {
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "project_root": _PROJECT_ROOT,
        "results": results,
        "rules_armed": rules_armed,
        "activity": activity,
        "log_hint": log_hint,
        "busy": busy,
        "alerts": alerts,
        "ondemand": {
            **ond,
            "tick_eta_sec": od_tick_eta,
            "tick_eta_text": _fmt_eta(od_tick_eta) if od_tick_eta else "—",
            "daily_eta_sec": od_daily_eta,
            "daily_eta_text": _fmt_eta(od_daily_eta) if od_daily_eta else "—",
            "eta_note": "粗算（默认约 %.0f 秒/分时、%.0f 秒/日线）"
            % (DEFAULT_SEC_PER_ONDEMAND_TICK, DEFAULT_SEC_PER_ONDEMAND_DAILY),
        },
        "tick_full": tick_full,
        "daily_manifest": daily,
        "after_rank": after,
        "schedule": schedule,
    }


def snapshot_summary_text(snap: Optional[Dict[str, Any]] = None) -> str:
    s = snap or build_snapshot()
    r = s.get("results") or {}
    a = s.get("rules_armed") or {}
    lines = [
        "=== 大 QMT 状态 %s ===" % s.get("generated_at"),
        "在线: %s | 活动: %s | 繁忙: %s(%s)"
        % (
            "是" if r.get("online") else "否",
            s.get("activity"),
            (s.get("busy") or {}).get("label"),
            (s.get("busy") or {}).get("score"),
        ),
        "results.json: %s | stocks=%s 持仓=%s 柜台单=%s | 心跳=%s"
        % (
            r.get("size_text") or "—",
            r.get("n_stocks"),
            r.get("n_positions"),
            r.get("n_broker_orders"),
            ("%.0fs" % r["age_sec"]) if r.get("age_sec") is not None else "—",
        ),
        "rules_armed.json: %s | tasks=%s watch=%s pool=%s subscribe=%s"
        % (
            a.get("size_text") or "—",
            a.get("n_tasks"),
            a.get("n_watch"),
            a.get("n_pool_watch"),
            a.get("n_subscribe"),
        ),
        "按需: 日线待处理=%s 分时待处理=%s（分时预计剩余 %s）"
        % (
            (s.get("ondemand") or {}).get("daily_pending"),
            (s.get("ondemand") or {}).get("tick_pending"),
            (s.get("ondemand") or {}).get("tick_eta_text"),
        ),
        "分笔: 暂停=%s 队列日=%s 当前日=%s 预计剩余 %s"
        % (
            "是" if (s.get("tick_full") or {}).get("pause") else "否",
            (s.get("tick_full") or {}).get("manual_days"),
            (s.get("tick_full") or {}).get("active_day"),
            (s.get("tick_full") or {}).get("eta_text"),
        ),
        "日线清单: %s 回填=%s 起始=%s"
        % (
            status_cn((s.get("daily_manifest") or {}).get("status")),
            "是" if (s.get("daily_manifest") or {}).get("force_year") else "否",
            (s.get("daily_manifest") or {}).get("force_start_text") or "—",
        ),
        "盘后量能: 最近完成=%s 待跑=%s"
        % (
            (s.get("after_rank") or {}).get("last_completed_text") or "—",
            "是" if (s.get("after_rank") or {}).get("pending") else "否",
        ),
    ]
    for a in s.get("alerts") or []:
        lines.append("! %s" % _alert_text(a))
    return "\n".join(lines)


if __name__ == "__main__":
    print(snapshot_summary_text())
