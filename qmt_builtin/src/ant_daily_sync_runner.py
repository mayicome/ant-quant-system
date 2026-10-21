# -*- coding: utf-8 -*-
"""日线不再由 QMT 下载或写盘。

本模块只给尚未重载的旧「蚂蚁量化规则」留空入口：
15:35 定时、启动补跑、失败恢复、强制回补、按需同步都直接返回。
不再转去加载分笔或板块模块。
"""
import os

DAILY_SYNC_VERSION = "20261016.12"
_SYNC_RUNNING = False


def _env_truthy(name):
    v = str(os.environ.get(name) or "").strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def is_qmt_live_only():
    """实盘机跳过分笔全量和盘后量能。环境变量或 data/qmt_live_only.flag。"""
    if _env_truthy("ANT_QMT_LIVE_ONLY"):
        return True
    try:
        try:
            from ant_qmt_paths import PROJECT_ROOT
        except ImportError:
            from qmt_builtin.ant_qmt_paths import PROJECT_ROOT
        flag = os.path.join(str(PROJECT_ROOT).rstrip("\\/"), "data", "qmt_live_only.flag")
        return os.path.isfile(flag)
    except Exception:
        return False


def _daily_gate_open_for_tick():
    """日线不再挡分笔。"""
    return True


def register_daily_sync_timer(ContextInfo):
    return None


def schedule_failed_manifest_recovery_on_init():
    return None


def maybe_run_failed_manifest_recovery(ContextInfo=None):
    return False


def maybe_run_force_year_backfill(ContextInfo=None):
    return False


def process_on_demand_sync_requests(ContextInfo=None, max_items=1):
    return 0


def patch_daily_cache_today_from_tick_row(code, trade_d, row):
    return "skip"


def startup_catch_up(ContextInfo=None):
    return False


def daily_bar_sync(ContextInfo):
    """旧 15:35 定时仍会进这里。日线和分笔都不再下载。"""
    del ContextInfo
    return False


def maybe_catch_up_after_hours_pipeline(ContextInfo=None):
    """旧周期任务仍会进这里。不再写日线，也不再加载分笔。"""
    del ContextInfo
    return False
