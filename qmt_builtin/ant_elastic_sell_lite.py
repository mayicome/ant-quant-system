#coding:gbk
"""弹性卖出回落价（QMT 内嵌轻量版，兼容 Py3.6+）。"""
import configparser
import math
import os
import sys

DEFAULT_ROOM_BLEND_AT_DROP_LOW = 3.0
DEFAULT_ROOM_BLEND_AT_DROP_HIGH = 1.5
DROP_LOW_REF = 2.5
DROP_HIGH_REF = 5.0
# 主板 ST 涨跌幅调整日（与 utils/limit_ratio 一致）
_ST_MAIN_LIMIT_CHANGE = (2026, 7, 6)

_ELASTIC_GLOBAL_CACHE = None
# code -> (limit_up, pre_close)，避免每 tick 重复算
_LIMIT_CACHE = {}



def _repo_root():
    try:
        from ant_qmt_paths import PROJECT_ROOT

        root = str(PROJECT_ROOT or "").strip()
        if root and os.path.isdir(root):
            return root
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (
        os.path.dirname(os.path.dirname(here)),
        os.path.dirname(here),
        here,
    ):
        if os.path.isfile(os.path.join(cand, "data", "config.ini")):
            return cand
    return os.path.dirname(os.path.dirname(here))


class ElasticGlobalConfig(object):
    __slots__ = (
        "confirm_ticks",
        "cooldown_after_extreme_ticks",
        "dynamic_thresholds",
        "room_tight_pp",
        "drop_tight",
        "limit_minus_pp",
        "drop_scale",
        "max_drop_percent",
    )

    def __init__(
        self,
        confirm_ticks=4,
        cooldown_after_extreme_ticks=2,
        dynamic_thresholds=2,
        room_tight_pp=1.0,
        drop_tight=0.5,
        limit_minus_pp=1.0,
        drop_scale=0.35,
        max_drop_percent=3.5,
    ):
        self.confirm_ticks = int(confirm_ticks)
        self.cooldown_after_extreme_ticks = int(cooldown_after_extreme_ticks)
        self.dynamic_thresholds = int(dynamic_thresholds)
        self.room_tight_pp = float(room_tight_pp)
        self.drop_tight = float(drop_tight)
        self.limit_minus_pp = float(limit_minus_pp)
        self.drop_scale = float(drop_scale)
        self.max_drop_percent = float(max_drop_percent)


def load_elastic_global_config(force_reload=False):
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

        def _f(key, fallback):
            try:
                return float(cfg.get("Elastic", key, fallback=str(fallback)))
            except Exception:
                return fallback

        def _i(key, fallback):
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


def load_elastic_confirm_triple():
    g = load_elastic_global_config()
    return g.confirm_ticks, g.cooldown_after_extreme_ticks, g.dynamic_thresholds


def resolve_room_blend_start(rule):
    raw = rule.get("room_blend_start") if rule is not None else None
    if raw is not None:
        try:
            val = float(raw)
            if val > 0:
                return val
        except (TypeError, ValueError):
            pass
    try:
        drop = float((rule or {}).get("drop_percent") or DROP_LOW_REF)
    except (TypeError, ValueError):
        drop = DROP_LOW_REF
    if drop <= DROP_LOW_REF:
        return DEFAULT_ROOM_BLEND_AT_DROP_LOW
    if drop >= DROP_HIGH_REF:
        return DEFAULT_ROOM_BLEND_AT_DROP_HIGH
    w = (drop - DROP_LOW_REF) / (DROP_HIGH_REF - DROP_LOW_REF)
    return DEFAULT_ROOM_BLEND_AT_DROP_LOW + w * (
        DEFAULT_ROOM_BLEND_AT_DROP_HIGH - DEFAULT_ROOM_BLEND_AT_DROP_LOW
    )


def _code6(stock_code):
    s = str(stock_code or "").strip().upper()
    if "." in s:
        s = s.split(".", 1)[0]
    digits = "".join(ch for ch in s if ch.isdigit())
    return (digits[:6] if digits else s)[:6]


def _limit_ratio(stock_code, stock_name=""):
    """涨跌停幅度（小数）。优先仓库 utils.limit_ratio，失败则本地兜底。"""
    try:
        root = _repo_root()
        if root and root not in sys.path:
            sys.path.insert(0, root)
        from utils.limit_ratio import get_limit_ratio

        return float(get_limit_ratio(stock_code, stock_name or ""))
    except Exception:
        pass
    code = _code6(stock_code)
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("8", "4", "920")):
        return 0.30
    name_u = (stock_name or "").upper()
    if "ST" in name_u and not code.startswith(("300", "301", "688", "689", "8", "4", "920")):
        try:
            from datetime import date

            if date.today() < date(*_ST_MAIN_LIMIT_CHANGE):
                return 0.05
        except Exception:
            return 0.05
    return 0.10


def _stock_price_round(value, precision=2):
    """A 股标准四舍五入（与主程序 calculate_limit_prices 一致）。"""
    try:
        mult = 10 ** int(precision)
        return math.floor(float(value) * mult + 0.5) / mult
    except Exception:
        return round(float(value), 2)


def _compute_limit_up(stock_code, pre_close, stock_name=""):
    pc = float(pre_close or 0)
    if pc <= 0:
        return 0.0
    ratio = _limit_ratio(stock_code, stock_name)
    return _stock_price_round(pc * (1.0 + ratio), 2)


def resolve_limit_up_pre_close(stock_code, row=None, stock_name="", cache=None):
    """
    弹性卖出 room 公式用的涨停/昨收。

    与主程序 stock_chart_widget._best_sell_limit_pre_close 对齐：
    tick 缺 highLimit/lastClose 时，用昨收推算涨停，避免 dynamic_thresholds=2
    静默退回「满档 drop_percent」（不缩小弹性）。
    """
    row = row if isinstance(row, dict) else {}
    cache = _LIMIT_CACHE if cache is None else cache
    code = str(stock_code or "").strip().upper()
    c6 = _code6(code)

    def _f(*keys):
        for k in keys:
            raw = row.get(k)
            if raw is None:
                continue
            try:
                v = float(raw)
                if v > 0:
                    return v
            except (TypeError, ValueError):
                continue
        return 0.0

    limit_up = _f("highLimit", "upperLimit", "limitUp", "limit_up")
    pre_close = _f("lastClose", "preClose", "pre_close", "last_close")

    cached = cache.get(code) or cache.get(c6)
    if isinstance(cached, (tuple, list)) and len(cached) >= 2:
        if limit_up <= 0:
            try:
                limit_up = float(cached[0] or 0)
            except (TypeError, ValueError):
                pass
        if pre_close <= 0:
            try:
                pre_close = float(cached[1] or 0)
            except (TypeError, ValueError):
                pass

    if limit_up <= 0 and pre_close > 0:
        limit_up = _compute_limit_up(code or c6, pre_close, stock_name)

    if limit_up > 0 and pre_close > 0 and (code or c6):
        cache[code or c6] = (limit_up, pre_close)
        if c6 and c6 != code:
            cache[c6] = (limit_up, pre_close)
    return float(limit_up or 0), float(pre_close or 0)


def compute_best_sell_fallback(
    highest,
    trigger_price=0.0,
    drop_percent=2.5,
    room_blend_start=3.0,
    limit_up=0.0,
    pre_close=0.0,
    pullback_price=None,
    dynamic_thresholds=None,
    global_cfg=None,
    drop_scale=None,
    max_drop_percent=None,
):
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

    if dyn <= 0:
        return drop_nom, highest * (1.0 - drop_nom / 100.0)

    if dyn == 1:
        try:
            profit_pct = (
                max(0.0, (float(highest) / float(trigger_price) - 1.0) * 100.0)
                if trigger_price
                else 0.0
            )
        except Exception:
            profit_pct = 0.0
        ds = float(drop_scale if drop_scale is not None else cfg.drop_scale)
        md = float(max_drop_percent if max_drop_percent is not None else cfg.max_drop_percent)
        eff = min(md, drop_nom + profit_pct * ds)
        return eff, highest * (1.0 - eff / 100.0)

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
    highest, rule, limit_up=0.0, pre_close=0.0, global_cfg=None
):
    cfg = global_cfg or load_elastic_global_config()
    rule = rule or {}
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
