#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
弹性卖出（best_sell）回落价计算 — 回测与实盘共用。

dynamic_thresholds:
  0 = 静态：始终使用 drop_percent
  1 = 旧逻辑：按 (最高价/触发价) 动态放大回撤，cap max_drop_percent
  2 = 按距涨停 room_pp 分段（宽段 / 过渡 / 近板），需 limit_up、pre_close
"""

from __future__ import annotations

import configparser
import os
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

# 手动规则未填 room_blend_start 时的缺省（与策略 5% / 7.5% 档一致）
DEFAULT_ROOM_BLEND_AT_DROP_LOW = 3.0   # 小回撤档（如 2.5%）
DEFAULT_ROOM_BLEND_AT_DROP_HIGH = 1.5  # 大回撤档（如 5%）
DROP_LOW_REF = 2.5
DROP_HIGH_REF = 5.0


@dataclass(frozen=True)
class ElasticGlobalConfig:
    confirm_ticks: int = 4
    cooldown_after_extreme_ticks: int = 2
    dynamic_thresholds: int = 2
    room_tight_pp: float = 1.0
    drop_tight: float = 0.5
    limit_minus_pp: float = 1.0
    drop_scale: float = 0.35
    max_drop_percent: float = 3.5


_ELASTIC_GLOBAL_CACHE: Optional[ElasticGlobalConfig] = None


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_elastic_global_config(force_reload: bool = False) -> ElasticGlobalConfig:
    """从 data/config.ini [Elastic] 读取全局弹性参数。"""
    global _ELASTIC_GLOBAL_CACHE
    if _ELASTIC_GLOBAL_CACHE is not None and not force_reload:
        return _ELASTIC_GLOBAL_CACHE
    defaults = ElasticGlobalConfig()
    try:
        cfg_path = os.path.join(_repo_root(), "data", "config.ini")
        if not os.path.exists(cfg_path):
            _ELASTIC_GLOBAL_CACHE = defaults
            return _ELASTIC_GLOBAL_CACHE
        cfg = configparser.ConfigParser()
        cfg.read(cfg_path, encoding="utf-8-sig")
        if not cfg.has_section("Elastic"):
            _ELASTIC_GLOBAL_CACHE = defaults
            return _ELASTIC_GLOBAL_CACHE

        def _f(key: str, fallback: float) -> float:
            try:
                return float(cfg.get("Elastic", key, fallback=str(fallback)))
            except Exception:
                return fallback

        def _i(key: str, fallback: int) -> int:
            try:
                return int(cfg.get("Elastic", key, fallback=str(fallback)))
            except Exception:
                return fallback

        confirm_ticks = _i("confirm_ticks", defaults.confirm_ticks)
        cooldown_ticks = _i("cooldown_after_extreme_ticks", defaults.cooldown_after_extreme_ticks)
        dynamic_thresholds = _i("dynamic_thresholds", defaults.dynamic_thresholds)
        if confirm_ticks < 0:
            confirm_ticks = defaults.confirm_ticks
        if cooldown_ticks < 0:
            cooldown_ticks = 0
        _ELASTIC_GLOBAL_CACHE = ElasticGlobalConfig(
            confirm_ticks=confirm_ticks,
            cooldown_after_extreme_ticks=cooldown_ticks,
            dynamic_thresholds=dynamic_thresholds,
            room_tight_pp=_f("room_tight_pp", defaults.room_tight_pp),
            drop_tight=_f("drop_tight", defaults.drop_tight),
            limit_minus_pp=_f("limit_minus_pp", defaults.limit_minus_pp),
            drop_scale=_f("drop_scale", defaults.drop_scale),
            max_drop_percent=_f("max_drop_percent", defaults.max_drop_percent),
        )
        return _ELASTIC_GLOBAL_CACHE
    except Exception:
        _ELASTIC_GLOBAL_CACHE = defaults
        return _ELASTIC_GLOBAL_CACHE


def load_elastic_confirm_triple() -> Tuple[int, int, int]:
    """兼容旧接口：confirm_ticks, cooldown_ticks, dynamic_thresholds。"""
    g = load_elastic_global_config()
    return g.confirm_ticks, g.cooldown_after_extreme_ticks, g.dynamic_thresholds


def resolve_room_blend_start(rule: Mapping[str, Any]) -> float:
    """从规则读取 room_blend_start；缺省时按 drop_percent 在 2.5/5 档间插值。"""
    raw = rule.get("room_blend_start")
    if raw is not None:
        try:
            val = float(raw)
            if val > 0:
                return val
        except (TypeError, ValueError):
            pass
    try:
        drop = float(rule.get("drop_percent") or DROP_LOW_REF)
    except (TypeError, ValueError):
        drop = DROP_LOW_REF
    if drop <= DROP_LOW_REF:
        return DEFAULT_ROOM_BLEND_AT_DROP_LOW
    if drop >= DROP_HIGH_REF:
        return DEFAULT_ROOM_BLEND_AT_DROP_HIGH
    t = (drop - DROP_LOW_REF) / (DROP_HIGH_REF - DROP_LOW_REF)
    return DEFAULT_ROOM_BLEND_AT_DROP_LOW + t * (
        DEFAULT_ROOM_BLEND_AT_DROP_HIGH - DEFAULT_ROOM_BLEND_AT_DROP_LOW
    )


def compute_best_sell_fallback(
    highest: float,
    *,
    trigger_price: float,
    drop_percent: float,
    room_blend_start: float,
    limit_up: float = 0.0,
    pre_close: float = 0.0,
    pullback_price: Optional[float] = None,
    dynamic_thresholds: Optional[int] = None,
    global_cfg: Optional[ElasticGlobalConfig] = None,
    drop_scale: Optional[float] = None,
    max_drop_percent: Optional[float] = None,
) -> Tuple[float, float]:
    """
    计算有效回撤百分比与回落触发价。

    Returns:
        (eff_drop_percent, fallback_price)
    """
    if pullback_price is not None:
        try:
            pp = float(pullback_price)
            if pp > 0:
                return 0.0, pp
        except (TypeError, ValueError):
            pass

    if highest <= 0:
        return float(drop_percent), 0.0

    cfg = global_cfg or load_elastic_global_config()
    dyn = int(dynamic_thresholds if dynamic_thresholds is not None else cfg.dynamic_thresholds)
    drop_nom = float(drop_percent)

    # 静态
    if dyn <= 0:
        return drop_nom, highest * (1.0 - drop_nom / 100.0)

    # 旧动态（兼容 dynamic_thresholds=1）
    if dyn == 1:
        try:
            profit_pct = max(
                0.0, (float(highest) / float(trigger_price) - 1.0) * 100.0
            ) if trigger_price else 0.0
        except Exception:
            profit_pct = 0.0
        ds = float(drop_scale if drop_scale is not None else cfg.drop_scale)
        md = float(max_drop_percent if max_drop_percent is not None else cfg.max_drop_percent)
        eff = min(md, drop_nom + profit_pct * ds)
        return eff, highest * (1.0 - eff / 100.0)

    # room 分段（dynamic_thresholds=2）
    lu = float(limit_up or 0.0)
    pc = float(pre_close or 0.0)
    if lu <= 0 or pc <= 0:
        return drop_nom, highest * (1.0 - drop_nom / 100.0)

    room_pp = (lu - float(highest)) / pc * 100.0
    room_tight = float(cfg.room_tight_pp)
    drop_tight = float(cfg.drop_tight)
    limit_minus = float(cfg.limit_minus_pp)
    blend_start = float(room_blend_start)
    if blend_start <= room_tight + 1e-9:
        blend_start = room_tight + 0.5

    if room_pp >= blend_start - 1e-9:
        eff = drop_nom
    elif room_pp <= room_tight + 1e-9:
        eff = drop_tight
    else:
        w = (room_pp - room_tight) / (blend_start - room_tight)
        eff = drop_tight + w * (drop_nom - drop_tight)

    fb = float(highest) * (1.0 - eff / 100.0)
    if room_pp <= room_tight + 1e-9:
        floor_px = float(highest) - limit_minus * pc / 100.0
        fb = max(fb, floor_px)
    return eff, fb


def compute_best_sell_fallback_from_rule(
    highest: float,
    rule: Mapping[str, Any],
    *,
    limit_up: float = 0.0,
    pre_close: float = 0.0,
    global_cfg: Optional[ElasticGlobalConfig] = None,
) -> Tuple[float, float]:
    """从 rule / intent 字典计算回落价。"""
    cfg = global_cfg or load_elastic_global_config()
    blend = resolve_room_blend_start(rule)
    dyn = rule.get("dynamic_thresholds")
    try:
        dyn_int = int(dyn) if dyn is not None else None
    except (TypeError, ValueError):
        dyn_int = None
    return compute_best_sell_fallback(
        highest,
        trigger_price=float(rule.get("trigger_price") or 0),
        drop_percent=float(rule.get("drop_percent") or 0),
        room_blend_start=blend,
        limit_up=limit_up,
        pre_close=pre_close,
        pullback_price=rule.get("pullback_price"),
        dynamic_thresholds=dyn_int,
        global_cfg=cfg,
        drop_scale=rule.get("drop_scale"),
        max_drop_percent=rule.get("max_drop_percent"),
    )
