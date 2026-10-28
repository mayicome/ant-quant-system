# -*- coding: utf-8 -*-
"""从 TaskManager 导出 rules_armed.json（含 watch_codes），供 QMT 内置策略读取。"""
from __future__ import annotations

import json
import time
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from utils.ant_rules_io_ext import (
    RULES_VERSION,
    collect_subscribe_codes,
    default_paths,
    normalize_armed_task,
    normalize_watch_codes,
    save_json_atomic,
)


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _trade_date_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def _norm_code(raw: Any) -> str:
    code = str(raw or "").strip().upper()
    if not code:
        return ""
    if "." not in code and len(code) >= 6:
        if code.startswith(("0", "1", "3")):
            return f"{code}.SZ"
        if code.startswith(("5", "6")):
            return f"{code}.SH"
        if code.startswith(("4", "8", "920")):
            return f"{code}.BJ"
    return code


def _parse_params(task: Dict[str, Any]) -> Dict[str, Any]:
    params = task.get("params") or {}
    if isinstance(params, str):
        try:
            params = json.loads(params) if params else {}
        except (json.JSONDecodeError, TypeError, ValueError):
            params = {}
    return params if isinstance(params, dict) else {}


def _rule_executed(rule: Dict[str, Any]) -> bool:
    if rule.get("executed"):
        return True
    if rule.get("scheduled_clear_executed"):
        return True
    if rule.get("true_breakthrough_passed"):
        return True
    if str(rule.get("halt_reason") or "").strip():
        return True
    reason = str(rule.get("executed_reason") or "").strip()
    return bool(reason)


def _rule_meta(task_id: str, rule: Dict[str, Any]) -> Dict[str, Any]:
    """武装任务 metadata：规则名 + 可选腿键（供成交后写 filled_legs）。"""
    meta: Dict[str, Any] = {
        "parent_task_id": task_id,
        "rule_name": str(rule.get("name") or ""),
    }
    lk = str(rule.get("leg_key") or "").strip()
    if lk:
        meta["leg_key"] = lk
    return meta


def _buy_export_volume(rule: Dict[str, Any]) -> int:
    """买入武装数量：等资金模式下用剩余量。"""
    try:
        base = int(rule.get("volume") or 0)
    except (TypeError, ValueError):
        base = 0
    if not bool(rule.get("cash_wait_active")):
        return base
    try:
        rem = rule.get("remaining_volume")
        if rem is not None:
            return max(0, int(rem))
    except (TypeError, ValueError):
        pass
    return base


def _armed_buy_extras(rule: Dict[str, Any]) -> Dict[str, Any]:
    """再次执行后的等资金字段，随武装任务下发。"""
    if not bool(rule.get("cash_wait_active")):
        return {}
    out: Dict[str, Any] = {"cash_wait_active": True}
    try:
        rem = rule.get("remaining_volume")
        if rem is not None:
            out["remaining_volume"] = max(0, int(rem))
    except (TypeError, ValueError):
        pass
    return out


def _rule_early_order_enabled(rule: dict, global_default: bool) -> bool:
    """规则级提前下单快照；缺字段时用全局默认（兼容旧规则）。"""
    if isinstance(rule, dict) and "early_order_enabled" in rule:
        return bool(rule.get("early_order_enabled"))
    return bool(global_default)


def _stock_price_round(value: float, precision: int) -> float:
    import math

    multiplier = 10 ** int(precision)
    return math.floor(float(value) * multiplier + 0.5) / multiplier


def _clamp_night_trigger_for_session(
    task_manager,
    stock_code: str,
    rule_type: str,
    trigger: float,
) -> float:
    """盘后导出夜市价时，按次日昨收（今日收盘）重夹涨跌停，避免仍用盘中跌停。"""
    try:
        from utils.trading_day import is_after_reference_switch
        from utils.session_prev_close import resolve_session_prev_close
        from utils.limit_ratio import get_limit_ratio
        from core.utils.security_type import SecurityTypeUtil

        if not is_after_reference_switch():
            return float(trigger)
        last_px = 0.0
        try:
            last_px = float(
                (getattr(task_manager, "latest_prices", {}) or {}).get(stock_code, 0) or 0
            )
        except Exception:
            last_px = 0.0
        stored = 0.0
        try:
            if hasattr(task_manager, "get_pre_close_price"):
                stored = float(task_manager.get_pre_close_price(stock_code) or 0)
            else:
                stored = float(
                    (getattr(task_manager, "pre_close_prices", {}) or {}).get(stock_code, 0)
                    or 0
                )
        except Exception:
            stored = 0.0
        pc = resolve_session_prev_close(
            stock_code, qmt_last_close=stored, last_price=last_px
        )
        if pc <= 0:
            return float(trigger)
        ratio = float(get_limit_ratio(stock_code, "") or 0.1)
        precision = SecurityTypeUtil.get_price_precision(stock_code)
        lu = _stock_price_round(pc * (1.0 + ratio), precision)
        ld = _stock_price_round(pc * (1.0 - ratio), precision)
        px = float(trigger)
        if rule_type == "night_sell":
            if ld > 0 and px < ld:
                return ld
            if lu > 0 and px > lu:
                return lu
        elif rule_type == "night_buy":
            if lu > 0 and px > lu:
                return lu
            if ld > 0 and px < ld:
                return ld
        return px
    except Exception:
        return float(trigger)


def build_armed_tasks(task_manager) -> List[Dict[str, Any]]:
    """将运行中的单点买卖、突破买卖规则导出为 armed tasks。"""
    from core.trading_config import (
        breakthrough_buy_require_break_below_trigger,
        breakthrough_buy_require_true_breakthrough,
    )

    require_tb = breakthrough_buy_require_true_breakthrough(default=False)
    require_break_below = breakthrough_buy_require_break_below_trigger(default=False)
    early_default = _read_early_order_enabled()
    armed: List[Dict[str, Any]] = []

    for task in (getattr(task_manager, "tasks", None) or {}).values():
        if not isinstance(task, dict):
            continue
        params = _parse_params(task)
        if not params.get("task_running"):
            continue
        stock_code = _norm_code(task.get("stock_code"))
        if not stock_code:
            continue
        task_id = str(task.get("task_id") or stock_code)
        rules = params.get("rules") or []
        if not isinstance(rules, list):
            continue

        for idx, rule in enumerate(rules):
            if not isinstance(rule, dict):
                continue
            rule_type = str(rule.get("rule_type") or rule.get("type") or "").strip()
            if rule_type not in (
                "single_buy",
                "single_sell",
                "breakthrough_buy",
                "breakthrough_sell",
                "best_sell",
                "best_buy",
                "cage_buy",
                "cage_sell",
                "grid_buy",
                "grid_sell",
                "scheduled_clear",
                "night_buy",
                "night_sell",
            ):
                continue
            if rule.get("enabled") is False:
                continue
            # 延迟激活 / 清仓T点分支未决议：不武装，避免 QMT 侧提前触发
            try:
                from core.rule_activation import rule_activation_allows_trigger

                if not rule_activation_allows_trigger(rule):
                    continue
            except Exception:
                pass
            # 突破买入始终武装；是否判真突破由规则快照 require_true_breakthrough 决定
            rule_id = str(rule.get("id") or rule.get("name") or idx)
            if rule_type in ("night_buy", "night_sell"):
                trigger = float(rule.get("price") or rule.get("trigger_price") or 0)
                volume = int(rule.get("volume") or 0)
                if trigger <= 0 or volume <= 0:
                    continue
                if _rule_executed(rule):
                    continue
                trigger = _clamp_night_trigger_for_session(
                    task_manager, stock_code, rule_type, trigger
                )
                night_row = {
                    "task_id": f"{task_id}:{rule_id}",
                    "stock_code": stock_code,
                    "rule_type": rule_type,
                    "strategy_name": str(
                        task.get("strategy")
                        or ("夜市买入" if rule_type == "night_buy" else "夜市卖出")
                    ),
                    "trigger_price": trigger,
                    "enabled": True,
                    "max_volume": (
                        _buy_export_volume(rule) if rule_type == "night_buy" else volume
                    ),
                    "metadata": _rule_meta(task_id, rule),
                }
                if rule_type == "night_buy":
                    night_row.update(_armed_buy_extras(rule))
                armed.append(normalize_armed_task(night_row))
                continue
            if rule_type == "scheduled_clear":
                trigger = float(rule.get("price") or rule.get("trigger_price") or 0)
                volume = int(rule.get("volume") or 0)
                if trigger <= 0 or volume <= 0:
                    continue
                if _rule_executed(rule):
                    continue
                if rule.get("smart_sell_active") or (rule.get("smart_sell") or {}).get("active"):
                    continue
                time_str = str(rule.get("scheduled_clear_time") or "14:56:00").strip()
                eff = str(rule.get("scheduled_clear_effective_date") or "").strip()
                if not eff:
                    try:
                        from core.scheduled_clear_manager import (
                            resolve_scheduled_clear_effective_date,
                        )

                        eff = resolve_scheduled_clear_effective_date(rule).strftime(
                            "%Y-%m-%d"
                        )
                    except Exception:
                        eff = datetime.now().strftime("%Y-%m-%d")
                armed.append(
                    normalize_armed_task(
                        {
                            "task_id": f"{task_id}:{rule_id}",
                            "stock_code": stock_code,
                            "rule_type": "scheduled_clear",
                            "strategy_name": str(
                                task.get("strategy") or "定时清仓"
                            ),
                            "trigger_price": trigger,
                            "scheduled_clear_time": time_str,
                            "scheduled_clear_effective_date": eff,
                            "enabled": True,
                            "max_volume": volume,
                            "metadata": _rule_meta(task_id, rule),
                        }
                    )
                )
                continue
            if rule_type in ("grid_buy", "grid_sell"):
                start_price = float(rule.get("start_price") or 0)
                end_price = float(rule.get("end_price") or 0)
                num_grids = int(rule.get("num_grids") or 2)
                vol_pg = int(rule.get("volume_per_grid") or 0)
                if start_price <= 0 or end_price <= 0 or num_grids < 1 or vol_pg <= 0:
                    continue
                if rule_type == "grid_buy" and start_price <= end_price:
                    continue
                if rule_type == "grid_sell" and start_price >= end_price:
                    continue
                executed_grids = []
                for x in rule.get("executed_grids") or []:
                    try:
                        executed_grids.append(int(x))
                    except (TypeError, ValueError):
                        pass
                # 全部点位已执行则不再武装
                if _rule_executed(rule) or len(set(executed_grids)) >= num_grids + 1:
                    continue
                eo = _rule_early_order_enabled(rule, early_default)
                if "early_order_enabled" not in rule:
                    rule["early_order_enabled"] = eo
                armed.append(
                    normalize_armed_task(
                        {
                            "task_id": f"{task_id}:{rule_id}",
                            "stock_code": stock_code,
                            "rule_type": rule_type,
                            "strategy_name": str(
                                task.get("strategy")
                                or ("网格买入" if rule_type == "grid_buy" else "网格卖出")
                            ),
                            "start_price": start_price,
                            "end_price": end_price,
                            "num_grids": num_grids,
                            "grid_step": float(rule.get("grid_step") or 0),
                            "volume_per_grid": vol_pg,
                            "executed_grids": executed_grids,
                            "trigger_price": start_price,
                            "enabled": True,
                            "max_volume": vol_pg,
                            "early_order_enabled": eo,
                            "halt_on_open_gain": bool(rule.get("halt_on_open_gain"))
                            if rule_type == "grid_buy"
                            else False,
                            "metadata": _rule_meta(task_id, rule),
                        }
                    )
                )
                continue
            volume = int(rule.get("volume") or 0)
            buy_volume = _buy_export_volume(rule)
            buy_extras = _armed_buy_extras(rule)
            # 弹性卖出允许 volume=0 表示清仓
            if volume <= 0 and rule_type != "best_sell":
                continue
            if volume < 0:
                continue
            if rule_type in ("cage_buy", "cage_sell"):
                price_low = float(rule.get("price_low") or 0)
                price_high = float(rule.get("price_high") or 0)
                if price_low <= 0 or price_high <= price_low:
                    continue
                cage_row = {
                    "task_id": f"{task_id}:{rule_id}",
                    "stock_code": stock_code,
                    "rule_type": rule_type,
                    "strategy_name": str(
                        task.get("strategy")
                        or ("笼子买入" if rule_type == "cage_buy" else "笼子卖出")
                    ),
                    "price_low": price_low,
                    "price_high": price_high,
                    "wall_thickness": float(rule.get("wall_thickness") or 0),
                    "cage_entered": bool(rule.get("cage_entered")),
                    "trigger_price": price_low,
                    "enabled": not _rule_executed(rule),
                    "max_volume": buy_volume if rule_type == "cage_buy" else volume,
                    "halt_on_open_gain": bool(rule.get("halt_on_open_gain"))
                    if rule_type == "cage_buy"
                    else False,
                    "metadata": _rule_meta(task_id, rule),
                }
                if rule_type == "cage_buy":
                    cage_row.update(buy_extras)
                armed.append(normalize_armed_task(cage_row))
                continue
            trigger = float(rule.get("trigger_price") or rule.get("price") or 0)
            if trigger <= 0:
                continue
            if rule_type == "single_buy":
                strategy_name = str(task.get("strategy") or "单点买入")
                eo = _rule_early_order_enabled(rule, early_default)
                if "early_order_enabled" not in rule:
                    rule["early_order_enabled"] = eo
                armed_row = {
                    "task_id": f"{task_id}:{rule_id}",
                    "stock_code": stock_code,
                    "rule_type": "single_buy",
                    "strategy_name": strategy_name,
                    "trigger_price": trigger,
                    "require_break_below": False,
                    "break_below_trigger_done": False,
                    "enabled": not _rule_executed(rule),
                    "max_volume": buy_volume,
                    "early_order_enabled": eo,
                    "halt_on_open_gain": bool(rule.get("halt_on_open_gain")),
                    "metadata": _rule_meta(task_id, rule),
                }
                armed_row.update(buy_extras)
                if rule.get("wait_unseal"):
                    armed_row["wait_unseal"] = True
                if rule.get("fill_at_limit_up"):
                    armed_row["fill_at_limit_up"] = True
                if rule.get("open_buy_ask"):
                    armed_row["open_buy_ask"] = True
                try:
                    lu = float(rule.get("limit_up") or 0)
                    if lu > 0:
                        armed_row["limit_up"] = lu
                except (TypeError, ValueError):
                    pass
                armed.append(normalize_armed_task(armed_row))
                continue
            if rule_type == "single_sell":
                strategy_name = str(task.get("strategy") or "单点卖出")
                eo = _rule_early_order_enabled(rule, early_default)
                if "early_order_enabled" not in rule:
                    rule["early_order_enabled"] = eo
                armed.append(
                    normalize_armed_task(
                        {
                            "task_id": f"{task_id}:{rule_id}",
                            "stock_code": stock_code,
                            "rule_type": "single_sell",
                            "strategy_name": strategy_name,
                            "trigger_price": trigger,
                            "require_break_below": False,
                            "break_below_trigger_done": False,
                            "enabled": not _rule_executed(rule),
                            "max_volume": volume,
                            "early_order_enabled": eo,
                            "metadata": _rule_meta(task_id, rule),
                        }
                    )
                )
                continue
            if rule_type == "best_sell":
                armed.append(
                    normalize_armed_task(
                        {
                            "task_id": f"{task_id}:{rule_id}",
                            "stock_code": stock_code,
                            "rule_type": "best_sell",
                            "strategy_name": str(task.get("strategy") or "弹性卖出"),
                            "trigger_price": trigger,
                            "drop_percent": float(rule.get("drop_percent") or 2.5),
                            "room_blend_start": rule.get("room_blend_start"),
                            "pullback_price": rule.get("pullback_price"),
                            "confirm_ticks": rule.get("confirm_ticks"),
                            "cooldown_after_extreme_ticks": rule.get(
                                "cooldown_after_extreme_ticks"
                            ),
                            "dynamic_thresholds": rule.get("dynamic_thresholds"),
                            "enabled": not _rule_executed(rule),
                            "max_volume": volume,
                            "metadata": _rule_meta(task_id, rule),
                        }
                    )
                )
                continue
            if rule_type == "best_buy":
                best_buy_row = {
                    "task_id": f"{task_id}:{rule_id}",
                    "stock_code": stock_code,
                    "rule_type": "best_buy",
                    "strategy_name": str(task.get("strategy") or "弹性买入"),
                    "trigger_price": trigger,
                    "rise_percent": float(rule.get("rise_percent") or 0.3),
                    "rise_scale": rule.get("rise_scale"),
                    "max_rise_percent": rule.get("max_rise_percent"),
                    "confirm_ticks": rule.get("confirm_ticks"),
                    "cooldown_after_extreme_ticks": rule.get(
                        "cooldown_after_extreme_ticks"
                    ),
                    "dynamic_thresholds": rule.get("dynamic_thresholds"),
                    "halt_on_open_gain": bool(rule.get("halt_on_open_gain")),
                    "enabled": not _rule_executed(rule),
                    "max_volume": buy_volume,
                    "metadata": _rule_meta(task_id, rule),
                }
                best_buy_row.update(buy_extras)
                armed.append(normalize_armed_task(best_buy_row))
                continue
            if rule_type == "breakthrough_sell":
                # 与图表原逻辑一致：价格下穿/跌破触发价即卖，无需「先上破」
                armed.append(
                    normalize_armed_task(
                        {
                            "task_id": f"{task_id}:{rule_id}",
                            "stock_code": stock_code,
                            "rule_type": "breakthrough_sell",
                            "strategy_name": str(task.get("strategy") or "突破卖出"),
                            "trigger_price": trigger,
                            "require_break_above": False,
                            "break_above_trigger_done": False,
                            "enabled": not _rule_executed(rule),
                            "max_volume": volume,
                            "metadata": _rule_meta(task_id, rule),
                        }
                    )
                )
                continue
            armed.append(
                normalize_armed_task(
                    {
                        "task_id": f"{task_id}:{rule_id}",
                        "stock_code": stock_code,
                        "rule_type": "breakthrough_buy",
                        "strategy_name": str(task.get("strategy") or "突破买入"),
                        "trigger_price": trigger,
                        "price": float(rule.get("price") or trigger or 0),
                        "require_break_below": (
                            bool(rule.get("require_break_below"))
                            if "require_break_below" in rule
                            else bool(require_break_below)
                        ),
                        "require_true_breakthrough": (
                            bool(rule.get("require_true_breakthrough"))
                            if "require_true_breakthrough" in rule
                            else bool(require_tb)
                        ),
                        "break_below_trigger_done": bool(rule.get("break_below_trigger_done")),
                        "enabled": not _rule_executed(rule),
                        "true_breakthrough_cond1_mode": rule.get("true_breakthrough_cond1_mode")
                        or "tick3",
                        "true_breakthrough_window_sec": rule.get("true_breakthrough_window_sec"),
                        "band_low": rule.get("band_low"),
                        "band_high": rule.get("band_high"),
                        "band_accept_low": rule.get("band_accept_low")
                        if rule.get("band_accept_low") is not None
                        else rule.get("accept_band_low"),
                        "max_volume": buy_volume,
                        "halt_on_open_gain": bool(rule.get("halt_on_open_gain")),
                        "metadata": _rule_meta(task_id, rule),
                        **buy_extras,
                    }
                )
            )
            # 缺字段时一次性回写到规则（不覆盖已有快照）
            if "require_true_breakthrough" not in rule:
                rule["require_true_breakthrough"] = bool(require_tb)
            if "require_break_below" not in rule:
                rule["require_break_below"] = bool(require_break_below)
            # 价格带规则：强制真突破 window（与回测/图表一致）
            if rule.get("band_low") is not None and rule.get("band_high") is not None:
                rule["require_true_breakthrough"] = True
                if not rule.get("true_breakthrough_cond1_mode"):
                    rule["true_breakthrough_cond1_mode"] = "window"
                if rule.get("true_breakthrough_window_sec") is None:
                    rule["true_breakthrough_window_sec"] = 45
                rule["require_break_below"] = False
    return armed


def collect_watch_codes(task_manager, qmt_adapter=None) -> List[str]:
    """任务股票 + 持仓股票并集。"""
    codes: Set[str] = set()

    for task in (getattr(task_manager, "tasks", None) or {}).values():
        if not isinstance(task, dict):
            continue
        code = _norm_code(task.get("stock_code"))
        if code:
            codes.add(code)

    positions = {}
    if qmt_adapter is not None:
        positions = getattr(qmt_adapter, "cached_positions", None) or {}
    if isinstance(positions, dict):
        for raw_code in positions.keys():
            code = _norm_code(raw_code)
            if code:
                codes.add(code)

    return normalize_watch_codes(sorted(codes))


def _read_early_order_enabled() -> bool:
    try:
        import configparser

        ini_path = os.path.join(project_root(), "data", "config.ini")
        if not os.path.isfile(ini_path):
            return False
        cfg = configparser.ConfigParser()
        cfg.read(ini_path, encoding="utf-8")
        if cfg.has_option("Trading", "early_order"):
            value = str(cfg.get("Trading", "early_order") or "0").strip().lower()
            return value in ("1", "true", "yes", "on")
    except Exception:
        pass
    return False


def _read_min_buy_amount() -> float:
    """全局最小买入金额（元）：卡本笔价×量；≤0 不限制。缺省 5000。"""
    try:
        import configparser

        ini_path = os.path.join(project_root(), "data", "config.ini")
        if not os.path.isfile(ini_path):
            return 5000.0
        cfg = configparser.ConfigParser()
        cfg.read(ini_path, encoding="utf-8")
        if cfg.has_option("Trading", "min_buy_amount"):
            return max(0.0, float(cfg.get("Trading", "min_buy_amount") or 0))
        return 5000.0
    except Exception:
        return 5000.0



def _read_buy_shrink_min_ratio_pct() -> float:
    """买入缩量最低比例(%)：能买股数/计划股数低于此值则整笔 no_cash。缺省 0=与现在一样。"""
    try:
        import configparser

        ini_path = os.path.join(project_root(), "data", "config.ini")
        if not os.path.isfile(ini_path):
            return 0.0
        cfg = configparser.ConfigParser()
        cfg.read(ini_path, encoding="utf-8")
        if cfg.has_option("Trading", "buy_shrink_min_ratio_pct"):
            return max(0.0, min(100.0, float(cfg.get("Trading", "buy_shrink_min_ratio_pct") or 0)))
        return 0.0
    except Exception:
        return 0.0


def _read_buy_block_window() -> Dict[str, Any]:
    """开盘禁买时间窗：enabled + start/end (HH:MM:SS)。"""
    out = {
        "buy_block_window_enabled": False,
        "buy_block_start": "09:30:00",
        "buy_block_end": "09:31:30",
    }
    try:
        import configparser

        ini_path = os.path.join(project_root(), "data", "config.ini")
        if not os.path.isfile(ini_path):
            return out
        cfg = configparser.ConfigParser()
        cfg.read(ini_path, encoding="utf-8")
        if not cfg.has_section("Trading"):
            return out
        raw = str(cfg.get("Trading", "buy_block_window_enabled", fallback="0") or "0")
        out["buy_block_window_enabled"] = raw.strip().lower() in ("1", "true", "yes", "on")
        out["buy_block_start"] = str(
            cfg.get("Trading", "buy_block_start", fallback="09:30:00") or "09:30:00"
        ).strip()
        out["buy_block_end"] = str(
            cfg.get("Trading", "buy_block_end", fallback="09:31:30") or "09:31:30"
        ).strip()
    except Exception:
        pass
    return out



def armed_parent_task_ids(armed_tasks) -> Set[str]:
    """从武装导出行提取父任务 id（metadata.parent_task_id 或 task_id 冒号前缀）。"""
    out: Set[str] = set()
    for row in armed_tasks or []:
        if not isinstance(row, dict):
            continue
        meta = row.get("metadata") or {}
        parent = ""
        if isinstance(meta, dict):
            parent = str(meta.get("parent_task_id") or "").strip()
        if not parent:
            tid = str(row.get("task_id") or "").strip()
            parent = tid.split(":", 1)[0].strip() if tid else ""
        if parent:
            out.add(parent)
    return out


def _stash_last_armed(task_manager, armed_tasks) -> None:
    if task_manager is None:
        return
    try:
        task_manager._last_rules_armed_tasks = list(armed_tasks or [])
        task_manager._last_rules_armed_parent_ids = armed_parent_task_ids(armed_tasks)
    except Exception:
        pass


def _read_armed_tasks_from_disk(project_root_override: Optional[str] = None) -> List[Dict[str, Any]]:
    root = project_root_override or project_root()
    rules_path, _ = default_paths(root)
    try:
        if not os.path.isfile(rules_path):
            return []
        with open(rules_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            tasks = data.get("tasks") or []
            return tasks if isinstance(tasks, list) else []
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        pass
    return []




def qmt_armed_ack_status(
    task_id: str,
    *,
    publish_ts: float = 0.0,
    grace_sec: float = 6.0,
    alive_sec: float = 90.0,
) -> str:
    """第二道门：QMT 是否确认吃到武装。返回 confirmed/pending/failed。"""
    tid = str(task_id or "").strip()
    now = time.time()
    try:
        from utils.qmt_formula_result_log import (
            armed_ack,
            armed_ack_at,
            heartbeat_at,
            heartbeat_seen,
            qmt_strategy_alive,
        )
    except Exception:
        if publish_ts and (now - float(publish_ts)) < float(grace_sec):
            return "pending"
        return "failed"

    alive = False
    try:
        alive = bool(qmt_strategy_alive(alive_sec))
    except Exception:
        alive = False
    if not alive:
        try:
            hb = float(heartbeat_at() or 0) if heartbeat_seen() else 0.0
            ack = float(armed_ack_at() or 0)
            latest = max(hb, ack)
            alive = latest > 0 and (now - latest) <= float(alive_sec)
        except Exception:
            alive = False

    ack_at = 0.0
    ack = {}
    try:
        ack_at = float(armed_ack_at() or 0)
        ack = armed_ack() or {}
    except Exception:
        ack_at = 0.0
        ack = {}

    pub = float(publish_ts or 0)
    in_grace = bool(pub > 0 and (now - pub) < float(grace_sec))

    if not alive:
        return "pending" if in_grace else "failed"

    ids = set()
    raw_ids = ack.get("ids") if isinstance(ack, dict) else None
    if isinstance(raw_ids, list):
        ids = {str(x).strip() for x in raw_ids if str(x or "").strip()}

    ack_after_pub = (pub <= 0) or (ack_at >= pub - 0.5)
    if ids:
        if tid in ids and ack_after_pub:
            return "confirmed"
        if in_grace and not ack_after_pub:
            return "pending"
        if ack_after_pub and tid not in ids:
            return "failed"
        return "pending" if in_grace else "failed"

    if ack_after_pub and ack_at > 0:
        return "confirmed"
    return "pending" if in_grace else "failed"


def qmt_pause_ack_status(
    task_id: str,
    *,
    publish_ts: float = 0.0,
    grace_sec: float = 6.0,
    alive_sec: float = 90.0,
) -> str:
    """暂停/停止回执：confirmed=QMT 已不含该任务；pending；failed=仍武装或无回执。"""
    tid = str(task_id or "").strip()
    now = time.time()
    try:
        from utils.qmt_formula_result_log import (
            armed_ack,
            armed_ack_at,
            heartbeat_at,
            heartbeat_seen,
            qmt_strategy_alive,
        )
    except Exception:
        if publish_ts and (now - float(publish_ts)) < float(grace_sec):
            return "pending"
        return "failed"

    alive = False
    try:
        alive = bool(qmt_strategy_alive(alive_sec))
    except Exception:
        alive = False
    if not alive:
        try:
            hb = float(heartbeat_at() or 0) if heartbeat_seen() else 0.0
            ack = float(armed_ack_at() or 0)
            latest = max(hb, ack)
            alive = latest > 0 and (now - latest) <= float(alive_sec)
        except Exception:
            alive = False

    ack_at = 0.0
    ack = {}
    try:
        ack_at = float(armed_ack_at() or 0)
        ack = armed_ack() or {}
    except Exception:
        ack_at = 0.0
        ack = {}

    pub = float(publish_ts or 0)
    in_grace = bool(pub > 0 and (now - pub) < float(grace_sec))

    if not alive:
        return "pending" if in_grace else "failed"

    ids = set()
    raw_ids = ack.get("ids") if isinstance(ack, dict) else None
    if isinstance(raw_ids, list):
        ids = {str(x).strip() for x in raw_ids if str(x or "").strip()}

    ack_after_pub = (pub <= 0) or (ack_at >= pub - 0.5)
    if not ack_after_pub:
        return "pending" if in_grace else "failed"

    if ids:
        if tid in ids:
            return "failed"  # QMT 仍武装该任务
        return "confirmed"

    if ack_at > 0:
        return "confirmed"
    return "pending" if in_grace else "failed"



def _in_a_share_qmt_watch_session(now_ts=None) -> bool:
    """盘中（含集合竞价至 15:05）才把心跳失联当成运行途中告警，避免收盘无 tick 误报。"""
    from datetime import datetime
    from datetime import time as dt_time

    now = datetime.fromtimestamp(float(time.time() if now_ts is None else now_ts))
    t = now.time()
    return dt_time(9, 15) <= t <= dt_time(15, 5)


def apply_mid_run_qmt_liveness(
    task_manager,
    *,
    logger=None,
    alive_sec: float = 90.0,
):
    """运行途中盯 QMT 心跳：已确认运行中的任务，心跳过期则叹号；恢复则消叹号。不改运行/暂停。"""
    if task_manager is None:
        return []
    if getattr(task_manager, "_armed_reconcile_busy", False):
        return []

    try:
        from utils.qmt_formula_result_log import qmt_strategy_alive, poll as poll_formula
    except Exception:
        return []

    try:
        poll_formula()
    except Exception:
        pass

    alive = False
    try:
        alive = bool(qmt_strategy_alive(alive_sec))
    except Exception:
        alive = False

    in_session = _in_a_share_qmt_watch_session()
    changes = []
    tasks_map = getattr(task_manager, "tasks", None) or {}

    for task_id, task in list(tasks_map.items()):
        if not isinstance(task, dict):
            continue
        tid = str(task_id)
        params = task.get("params") if isinstance(task.get("params"), dict) else None
        if not isinstance(params, dict):
            continue
        if bool(params.get("pending_qmt_ack")):
            continue
        flag_running = bool(params.get("task_running"))
        status = str(task.get("status") or "")
        status_running = status in ("运行中", "已委托", "夜盘已委托")
        if not (flag_running or status_running):
            continue
        if bool(params.get("task_paused")) and not flag_running:
            continue

        stock_code = str(task.get("stock_code") or "")
        was_unconfirmed = bool(params.get("qmt_ack_unconfirmed"))

        if alive:
            if was_unconfirmed:
                params["qmt_ack_unconfirmed"] = False
                params["pending_qmt_ack"] = False
                task["params"] = params
                try:
                    task_manager.tasks[tid]["params"] = params
                    task_manager.task_params[tid] = params
                except Exception:
                    pass
                reason = "QMT心跳恢复已清除未确认"
                if logger is not None:
                    try:
                        logger.info("[%s] %s (task_id=%s)" % (stock_code, reason, tid))
                    except Exception:
                        pass
                changes.append(
                    {
                        "task_id": tid,
                        "stock_code": stock_code,
                        "running": True,
                        "paused": False,
                        "reason": reason,
                        "qmt_ack": "confirmed",
                        "unconfirmed": False,
                    }
                )
            continue

        if not in_session:
            continue
        if was_unconfirmed:
            continue
        params["qmt_ack_unconfirmed"] = True
        params["pending_qmt_ack"] = False
        task["params"] = params
        try:
            task_manager.tasks[tid]["params"] = params
            task_manager.task_params[tid] = params
        except Exception:
            pass
        reason = "QMT运行途中失联已标记未确认"
        if logger is not None:
            try:
                logger.warning("[%s] %s (task_id=%s)" % (stock_code, reason, tid))
            except Exception:
                pass
        changes.append(
            {
                "task_id": tid,
                "stock_code": stock_code,
                "running": True,
                "paused": False,
                "reason": reason,
                "qmt_ack": "failed",
                "unconfirmed": True,
            }
        )
    return changes


def reconcile_run_state_with_armed(
    task_manager,
    armed_tasks=None,
    *,
    logger=None,
    require_qmt_ack: bool = True,
    publish_ts: float = 0.0,
    qmt_grace_sec: float = 6.0,
) -> List[Dict[str, Any]]:
    """以武装集合确认运行态。

    本地武装为第一道；QMT ACK 为第二道。
    QMT 超时不再回退点击后的运行/暂停状态，只打 qmt_ack_unconfirmed（图表叹号）；
    晚到回执会清叹号。本地未进武装（熔断关光规则等）也不再自动回退未运行；
    运行态仅人工启停/删图表可改。意图只认 params.task_running，避免暂停被拉回。
    """
    if task_manager is None:
        return []
    if getattr(task_manager, "_armed_reconcile_busy", False):
        return []

    if armed_tasks is None:
        armed_tasks = getattr(task_manager, "_last_rules_armed_tasks", None)
    if armed_tasks is None:
        armed_tasks = _read_armed_tasks_from_disk()

    parent_ids = armed_parent_task_ids(armed_tasks)
    changes: List[Dict[str, Any]] = []
    tasks_map = getattr(task_manager, "tasks", None) or {}
    running_tasks = getattr(task_manager, "running_tasks", None) or {}

    task_manager._armed_reconcile_busy = True
    try:
        for task_id, task in list(tasks_map.items()):
            if not isinstance(task, dict):
                continue
            tid = str(task_id)
            params = task.get("params") if isinstance(task.get("params"), dict) else {}
            if not isinstance(params, dict):
                params = {}
                task["params"] = params

            in_armed = tid in parent_ids
            in_running_map = tid in running_tasks
            flag_running = bool(params.get("task_running"))
            status = str(task.get("status") or "")
            status_running = status in ("运行中", "已委托", "夜市已委托")
            # 运行意图只认人工写的 params.task_running（点启/停）。
            # 勿用 running_tasks/status 反推：暂停时尚未清 running_tasks，
            # 会误判仍要运行，未进武装分支又强制拉回「运行中」并刷 UI。
            intended_running = flag_running and not bool(params.get("task_paused"))
            stock_code = str(task.get("stock_code") or "")
            pub = float(
                publish_ts
                or getattr(task_manager, "_rules_armed_published_at", 0)
                or 0
            )
            was_unconfirmed = bool(params.get("qmt_ack_unconfirmed"))
            was_pending = bool(params.get("pending_qmt_ack"))

            def _apply_params(new_status=None):
                task["params"] = params
                status_changed = False
                if new_status is not None and str(task.get("status") or "") != str(new_status):
                    task["status"] = new_status
                    status_changed = True
                try:
                    task_manager.tasks[tid]["params"] = params
                    if status_changed:
                        task_manager.tasks[tid]["status"] = new_status
                    task_manager.task_params[tid] = params
                except Exception:
                    pass
                # 状态没变不发 UI，避免轮询武装确认刷「UI状态已更新为」
                if status_changed:
                    try:
                        task_manager.update_task_ui.emit(tid, "status", new_status)
                    except Exception:
                        pass

            def _emit(*, running, paused, reason, qmt_ack, unconfirmed):
                changes.append(
                    {
                        "task_id": tid,
                        "stock_code": stock_code,
                        "running": running,
                        "paused": paused,
                        "reason": reason,
                        "qmt_ack": qmt_ack,
                        "unconfirmed": bool(unconfirmed),
                    }
                )

            if in_armed and intended_running:
                qmt_state = "confirmed"
                if require_qmt_ack:
                    qmt_state = qmt_armed_ack_status(
                        tid,
                        publish_ts=pub,
                        grace_sec=qmt_grace_sec,
                    )

                if qmt_state == "failed":
                    # 不回退运行态；仅叹号。日志/UI 只在状态变化时发一次，避免刷屏。
                    params["task_running"] = True
                    params["task_paused"] = False
                    params["pending_qmt_ack"] = False
                    params["qmt_ack_unconfirmed"] = True
                    _apply_params("运行中")
                    reason = "QMT未确认已标记未确认"
                    if not was_unconfirmed:
                        if logger is not None:
                            try:
                                logger.warning(f"[{stock_code}] {reason} (task_id={tid})")
                            except Exception:
                                pass
                        _emit(
                            running=True,
                            paused=False,
                            reason=reason,
                            qmt_ack="failed",
                            unconfirmed=True,
                        )
                    continue

                need = (
                    (not flag_running)
                    or bool(params.get("task_paused"))
                    or status != "运行中"
                    or bool(params.get("pending_qmt_ack")) != (qmt_state == "pending")
                    or bool(params.get("qmt_ack_unconfirmed"))
                )
                if not need and qmt_state == "confirmed":
                    if was_unconfirmed or was_pending:
                        params["pending_qmt_ack"] = False
                        params["qmt_ack_unconfirmed"] = False
                        _apply_params(None)
                        reason = "QMT确认运行中"
                        if logger is not None:
                            try:
                                logger.info(f"[{stock_code}] {reason} (task_id={tid})")
                            except Exception:
                                pass
                        _emit(
                            running=True,
                            paused=False,
                            reason=reason,
                            qmt_ack="confirmed",
                            unconfirmed=False,
                        )
                    else:
                        params["pending_qmt_ack"] = False
                        params["qmt_ack_unconfirmed"] = False
                    continue

                params["task_running"] = True
                params["task_paused"] = False
                params["pending_qmt_ack"] = qmt_state == "pending"
                # 宽限期内只 pending，超时后才叹号
                params["qmt_ack_unconfirmed"] = False
                _apply_params("运行中")
                if qmt_state == "pending":
                    reason = "等待QMT确认"
                else:
                    reason = "QMT确认运行中"
                if logger is not None:
                    try:
                        logger.info(f"[{stock_code}] {reason} (task_id={tid})")
                    except Exception:
                        pass
                _emit(
                    running=True,
                    paused=False,
                    reason=reason,
                    qmt_ack=qmt_state,
                    unconfirmed=False,
                )
                continue

            if in_armed and not intended_running:
                # 点了暂停但本地仍在武装：保持未运行/暂停，标待确认（不拉回运行中）
                qmt_state = "failed"
                if require_qmt_ack:
                    qmt_state = qmt_pause_ack_status(
                        tid,
                        publish_ts=pub,
                        grace_sec=qmt_grace_sec,
                    )
                new_status = "未运行" if status_running else (status or "未运行")
                if qmt_state == "pending":
                    params["pending_qmt_ack"] = True
                    params["qmt_ack_unconfirmed"] = False
                    reason = "等待QMT确认暂停"
                    unconfirmed = False
                elif qmt_state == "confirmed":
                    params["pending_qmt_ack"] = False
                    params["qmt_ack_unconfirmed"] = True
                    reason = "本地仍武装已标记待确认"
                    unconfirmed = True
                else:
                    params["pending_qmt_ack"] = False
                    params["qmt_ack_unconfirmed"] = True
                    reason = "QMT未确认已标记待确认"
                    unconfirmed = True
                params["task_running"] = False
                params["task_paused"] = True
                _apply_params(new_status)
                if logger is not None:
                    try:
                        logger.warning(f"[{stock_code}] {reason} (task_id={tid})")
                    except Exception:
                        pass
                _emit(
                    running=False,
                    paused=True,
                    reason=reason,
                    qmt_ack=qmt_state,
                    unconfirmed=unconfirmed,
                )
                continue

            if not intended_running:
                # 已暂停且本地不在武装：宽限期内可等卸装回执；超时/失败不再给未运行任务打叹号
                # （否则熔断/武装未包含回退后，暂停 ACK 失败会把叹号永久卡住）
                now = time.time()
                in_grace = pub > 0 and (now - pub) < max(float(qmt_grace_sec) * 3.0, 20.0)
                track = was_pending or in_grace
                if require_qmt_ack and track:
                    pause_state = qmt_pause_ack_status(
                        tid,
                        publish_ts=pub,
                        grace_sec=qmt_grace_sec,
                    )
                    if pause_state == "confirmed":
                        if was_unconfirmed or was_pending:
                            params["pending_qmt_ack"] = False
                            params["qmt_ack_unconfirmed"] = False
                            _apply_params(None)
                            reason = "QMT确认已暂停"
                            if logger is not None:
                                try:
                                    logger.info(
                                        f"[{stock_code}] {reason} (task_id={tid})"
                                    )
                                except Exception:
                                    pass
                            _emit(
                                running=False,
                                paused=bool(params.get("task_paused")),
                                reason=reason,
                                qmt_ack="confirmed",
                                unconfirmed=False,
                            )
                        else:
                            params["pending_qmt_ack"] = False
                            params["qmt_ack_unconfirmed"] = False
                    elif pause_state == "pending":
                        if not was_pending or was_unconfirmed:
                            params["pending_qmt_ack"] = True
                            params["qmt_ack_unconfirmed"] = False
                            _apply_params(None)
                            _emit(
                                running=False,
                                paused=bool(params.get("task_paused")),
                                reason="等待QMT确认暂停",
                                qmt_ack="pending",
                                unconfirmed=False,
                            )
                        else:
                            params["pending_qmt_ack"] = True
                            params["qmt_ack_unconfirmed"] = False
                    else:
                        # 未运行且不在武装：卸装回执失败只清标志，不再打「未确认」
                        if was_unconfirmed or was_pending:
                            params["pending_qmt_ack"] = False
                            params["qmt_ack_unconfirmed"] = False
                            _apply_params(None)
                            reason = "未运行已忽略暂停回执失败"
                            if logger is not None:
                                try:
                                    logger.info(
                                        f"[{stock_code}] {reason} (task_id={tid})"
                                    )
                                except Exception:
                                    pass
                            _emit(
                                running=False,
                                paused=bool(params.get("task_paused")),
                                reason=reason,
                                qmt_ack="failed",
                                unconfirmed=False,
                            )
                        else:
                            params["pending_qmt_ack"] = False
                            params["qmt_ack_unconfirmed"] = False
                elif was_unconfirmed or was_pending:
                    # 宽限期外仍挂着叹号/pending：直接清掉
                    params["pending_qmt_ack"] = False
                    params["qmt_ack_unconfirmed"] = False
                    _apply_params(None)
                    reason = "未运行已清除陈旧未确认"
                    if logger is not None:
                        try:
                            logger.info(
                                f"[{stock_code}] {reason} (task_id={tid})"
                            )
                        except Exception:
                            pass
                    _emit(
                        running=False,
                        paused=bool(params.get("task_paused")),
                        reason=reason,
                        qmt_ack="",
                        unconfirmed=False,
                    )
                continue

            # 意图运行却未进武装：保持运行态，绝不自动卸（熔断关规则/暂无武装规则亦然；仅人工可停）
            # 已是运行中且无 pending/叹号时不要反复 _apply_params，否则 UI 状态日志刷屏
            need = (
                (not flag_running)
                or bool(params.get("task_paused"))
                or status != "运行中"
                or was_unconfirmed
                or was_pending
            )
            reason = "武装未包含但保持运行中"
            if not need:
                continue
            params["task_running"] = True
            params["task_paused"] = False
            params["pending_qmt_ack"] = False
            params["qmt_ack_unconfirmed"] = False
            _apply_params("运行中")
            if logger is not None:
                try:
                    logger.info(
                        f"[{stock_code}] {reason} (task_id={tid}, "
                        f"had_running_flag={flag_running}, in_running_tasks={in_running_map})"
                    )
                except Exception:
                    pass
            _emit(
                running=True,
                paused=False,
                reason=reason,
                qmt_ack="",
                unconfirmed=False,
            )
    finally:
        task_manager._armed_reconcile_busy = False

    return changes




def sync_rules_armed(
    task_manager,
    qmt_adapter=None,
    *,
    logger=None,
    project_root_override: Optional[str] = None,
) -> bool:
    """写入 data/rules_armed.json；有变化时返回 True。"""
    root = project_root_override or project_root()
    rules_path, _ = default_paths(root)
    # 策略生成器临时订阅超时未释放时，先清掉，避免数百只长期拖垮行情
    try:
        from utils.strategy_pool_watch import expire_stale_strategy_pool_watch

        expired = expire_stale_strategy_pool_watch(root=root)
        if expired and logger is not None:
            logger.info(
                "[rules_armed] 已释放过期 strategy_pool_watch %d 只", expired
            )
    except Exception as e:
        if logger is not None:
            logger.debug("expire strategy_pool_watch skipped: %s", e)
    tasks = build_armed_tasks(task_manager)
    watch_codes = collect_watch_codes(task_manager, qmt_adapter)
    pool_watch: List[str] = []
    orders_enabled = True
    try:
        if os.path.isfile(rules_path):
            with open(rules_path, "r", encoding="utf-8") as f:
                old_pre = json.load(f)
            if isinstance(old_pre, dict):
                pool_watch = normalize_watch_codes(old_pre.get("strategy_pool_watch"))
                if "orders_enabled" in old_pre:
                    orders_enabled = bool(old_pre.get("orders_enabled"))
    except (OSError, json.JSONDecodeError, TypeError):
        pool_watch = []
    subscribe_codes = collect_subscribe_codes(tasks, watch_codes, pool_watch)
    early_order_enabled = _read_early_order_enabled()
    min_buy_amount = _read_min_buy_amount()
    buy_shrink_min_ratio_pct = _read_buy_shrink_min_ratio_pct()
    buy_block = _read_buy_block_window()

    payload: Dict[str, Any] = {
        "version": RULES_VERSION,
        "trade_date": _trade_date_str(),
        "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "tasks": tasks,
        "watch_codes": watch_codes,
        "strategy_pool_watch": pool_watch,
        "subscribe_codes": subscribe_codes,
        "orders_enabled": orders_enabled,
        "early_order_enabled": early_order_enabled,
        "min_buy_amount": min_buy_amount,
        "buy_shrink_min_ratio_pct": buy_shrink_min_ratio_pct,
        "buy_block_window_enabled": bool(buy_block.get("buy_block_window_enabled")),
        "buy_block_start": str(buy_block.get("buy_block_start") or "09:30:00"),
        "buy_block_end": str(buy_block.get("buy_block_end") or "09:31:30"),
    }

    try:
        if os.path.isfile(rules_path):
            with open(rules_path, "r", encoding="utf-8") as f:
                old = json.load(f)
            old_sig = json.dumps(
                {
                    "tasks": old.get("tasks"),
                    "watch_codes": old.get("watch_codes"),
                    "early_order_enabled": bool(old.get("early_order_enabled")),
                    "min_buy_amount": float(old.get("min_buy_amount") or 0),
                    "buy_shrink_min_ratio_pct": float(old.get("buy_shrink_min_ratio_pct") or 0),
                    "buy_block_window_enabled": bool(old.get("buy_block_window_enabled")),
                    "buy_block_start": str(old.get("buy_block_start") or ""),
                    "buy_block_end": str(old.get("buy_block_end") or ""),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            new_sig = json.dumps(
                {
                    "tasks": tasks,
                    "watch_codes": watch_codes,
                    "early_order_enabled": early_order_enabled,
                    "min_buy_amount": float(min_buy_amount or 0),
                    "buy_shrink_min_ratio_pct": float(buy_shrink_min_ratio_pct or 0),
                    "buy_block_window_enabled": bool(
                        buy_block.get("buy_block_window_enabled")
                    ),
                    "buy_block_start": str(buy_block.get("buy_block_start") or ""),
                    "buy_block_end": str(buy_block.get("buy_block_end") or ""),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            if old_sig == new_sig:
                _stash_last_armed(task_manager, tasks)
                return False
    except (OSError, json.JSONDecodeError, TypeError):
        pass

    # 写盘前再读一遍 strategy_pool_watch / orders_enabled，避免与策略生成器竞态覆盖
    try:
        if os.path.isfile(rules_path):
            with open(rules_path, "r", encoding="utf-8") as f:
                fresh = json.load(f)
            if isinstance(fresh, dict):
                pool_watch = normalize_watch_codes(fresh.get("strategy_pool_watch"))
                if "orders_enabled" in fresh:
                    orders_enabled = bool(fresh.get("orders_enabled"))
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    subscribe_codes = collect_subscribe_codes(tasks, watch_codes, pool_watch)
    payload["strategy_pool_watch"] = pool_watch
    payload["subscribe_codes"] = subscribe_codes
    payload["orders_enabled"] = orders_enabled
    payload["updated_at"] = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    try:
        save_json_atomic(rules_path, payload)
        _stash_last_armed(task_manager, tasks)
        try:
            if task_manager is not None:
                task_manager._rules_armed_published_at = time.time()
        except Exception:
            pass
        if logger is not None:
            logger.info(
                f"[rules_armed] 已同步 tasks={len(tasks)} watch={len(watch_codes)} "
                f"pool={len(pool_watch)} early={early_order_enabled} "
                f"min_buy={min_buy_amount} "
                f"shrink_ratio={buy_shrink_min_ratio_pct} "
                f"buy_block={buy_block.get('buy_block_window_enabled')} "
                f"{buy_block.get('buy_block_start')}-{buy_block.get('buy_block_end')} "
                f"-> {rules_path}"
            )
        return True
    except OSError as e:
        # 写失败时仍用本次计算出的武装集做 UI 确认；必要时可读盘兜底
        _stash_last_armed(task_manager, tasks)
        if logger is not None:
            logger.error(f"[rules_armed] 写入失败: {e}")
        return False
