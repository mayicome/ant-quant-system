# -*- coding: utf-8 -*-
"""从 TaskManager 导出 rules_armed.json（含 watch_codes），供 QMT 内置策略读取。"""
from __future__ import annotations

import json
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
    reason = str(rule.get("executed_reason") or "").strip()
    return bool(reason)


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
                armed.append(
                    normalize_armed_task(
                        {
                            "task_id": f"{task_id}:{rule_id}",
                            "stock_code": stock_code,
                            "rule_type": rule_type,
                            "strategy_name": str(
                                task.get("strategy")
                                or ("夜市买入" if rule_type == "night_buy" else "夜市卖出")
                            ),
                            "trigger_price": trigger,
                            "enabled": True,
                            "max_volume": volume,
                            "metadata": {
                                "parent_task_id": task_id,
                                "rule_name": str(rule.get("name") or ""),
                            },
                        }
                    )
                )
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
                            "metadata": {
                                "parent_task_id": task_id,
                                "rule_name": str(rule.get("name") or ""),
                            },
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
                            "metadata": {
                                "parent_task_id": task_id,
                                "rule_name": str(rule.get("name") or ""),
                            },
                        }
                    )
                )
                continue
            volume = int(rule.get("volume") or 0)
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
                armed.append(
                    normalize_armed_task(
                        {
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
                            "max_volume": volume,
                            "metadata": {
                                "parent_task_id": task_id,
                                "rule_name": str(rule.get("name") or ""),
                            },
                        }
                    )
                )
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
                    "max_volume": volume,
                    "early_order_enabled": eo,
                    "metadata": {
                        "parent_task_id": task_id,
                        "rule_name": str(rule.get("name") or ""),
                    },
                }
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
                            "metadata": {
                                "parent_task_id": task_id,
                                "rule_name": str(rule.get("name") or ""),
                            },
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
                            "metadata": {
                                "parent_task_id": task_id,
                                "rule_name": str(rule.get("name") or ""),
                            },
                        }
                    )
                )
                continue
            if rule_type == "best_buy":
                armed.append(
                    normalize_armed_task(
                        {
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
                            "enabled": not _rule_executed(rule),
                            "max_volume": volume,
                            "metadata": {
                                "parent_task_id": task_id,
                                "rule_name": str(rule.get("name") or ""),
                            },
                        }
                    )
                )
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
                            "metadata": {
                                "parent_task_id": task_id,
                                "rule_name": str(rule.get("name") or ""),
                            },
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
                        "max_volume": volume,
                        "metadata": {
                            "parent_task_id": task_id,
                            "rule_name": str(rule.get("name") or ""),
                        },
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
        if logger is not None:
            logger.info(
                f"[rules_armed] 已同步 tasks={len(tasks)} watch={len(watch_codes)} "
                f"pool={len(pool_watch)} early={early_order_enabled} "
                f"min_buy={min_buy_amount} "
                f"buy_block={buy_block.get('buy_block_window_enabled')} "
                f"{buy_block.get('buy_block_start')}-{buy_block.get('buy_block_end')} "
                f"-> {rules_path}"
            )
        return True
    except OSError as e:
        if logger is not None:
            logger.error(f"[rules_armed] 写入失败: {e}")
        return False
