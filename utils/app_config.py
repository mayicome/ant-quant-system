# -*- coding: utf-8 -*-
"""data/config.ini：新机按缺省参数生成，已有键不覆盖，只补缺失项。

本机账号/路径留空，不进 Git。
"""
from __future__ import annotations

import configparser
import os
from collections import OrderedDict
from typing import Dict, Mapping, Optional, Tuple

# 段内键均为字符串。账号与本机路径留空，须用户填写。
DEFAULT_SECTIONS: "OrderedDict[str, OrderedDict[str, str]]" = OrderedDict(
    [
        (
            "DEFAULT",
            OrderedDict(
                [
                    ("sell_times", "2"),
                    ("trade_volume", "1000"),
                    ("max_days", "2"),
                    ("up_threshold", "3.0"),
                    ("down_threshold", "3.5"),
                ]
            ),
        ),
        (
            "Account",
            OrderedDict(
                [
                    ("account_id", ""),
                    ("qmt_mode", "builtin"),
                    ("path_qmt", ""),
                ]
            ),
        ),
        (
            "qmt_builtin",
            OrderedDict(
                [
                    ("qmt_python_dir", ""),
                ]
            ),
        ),
        (
            "Trading",
            OrderedDict(
                [
                    ("require_manual_approval", "1"),
                    ("early_order", "0"),
                    ("buy_block_window_enabled", "0"),
                    ("buy_block_start", "09:30:00"),
                    ("buy_block_end", "14:30:59"),
                    ("min_buy_amount", "5000"),
                    ("breakthrough_buy_require_true_breakthrough", "0"),
                    ("breakthrough_buy_require_break_below_trigger", "0"),
                    ("non_early_order_sell_smart_sell", "0"),
                    ("breakthrough_buy_probe_enabled", "0"),
                ]
            ),
        ),
        (
            "Elastic",
            OrderedDict(
                [
                    ("confirm_ticks", "4"),
                    ("cooldown_after_extreme_ticks", "2"),
                    ("dynamic_thresholds", "2"),
                    ("room_tight_pp", "1.0"),
                    ("drop_tight", "0.5"),
                    ("limit_minus_pp", "1.0"),
                    ("drop_scale", "0.35"),
                    ("max_drop_percent", "3.5"),
                ]
            ),
        ),
        (
            "Recent Days",
            OrderedDict(
                [
                    ("up_thresholds", "0.05, 0.08, 0.09"),
                    ("down_thresholds", "0.05, 0.08, 0.09"),
                ]
            ),
        ),
        (
            "Today Analyse",
            OrderedDict(
                [
                    ("limit_up_ask_price", "0"),
                    ("limit_up_ask_vol", "0"),
                    ("limit_up_bid_vol_threshold", "100000"),
                    ("normal_bid_vol_threshold", "50000"),
                    ("trade_volume_ratio", "0.8"),
                    ("accumulation_volume_threshold", "10000"),
                    ("accumulation_price_change", "0"),
                    ("accumulation_bid_vol_change", "50000"),
                    ("accumulation_pressure_ratio", "1.5"),
                    ("accumulation_strong_threshold", "50000"),
                    ("accumulation_medium_threshold", "20000"),
                    ("distribution_volume_threshold", "10000"),
                    ("distribution_price_change", "0"),
                    ("distribution_ask_vol_change", "50000"),
                    ("distribution_pressure_ratio", "0.7"),
                    ("distribution_strong_threshold", "50000"),
                    ("distribution_medium_threshold", "20000"),
                    ("wash_volume_threshold", "15000"),
                    ("wash_price_change_threshold", "0.01"),
                    ("wash_vol_change_diff", "10000"),
                    ("wash_strong_threshold", "30000"),
                    ("wash_medium_threshold", "20000"),
                    ("support_bid_vol_change", "100000"),
                    ("support_volume_threshold", "5000"),
                    ("smash_volume_threshold", "20000"),
                    ("smash_price_change_threshold", "-0.02"),
                    ("smash_ask_vol_change", "100000"),
                    ("ask_vol_threshold", "50000"),
                    ("volume_threshold", "0"),
                    ("use_dynamic_thresholds", "2"),
                    ("volume_threshold_multiplier", "30.0"),
                    ("min_volume_threshold", "100"),
                    ("bid_vol_threshold_fixed", "0"),
                    ("ask_vol_threshold_fixed", "0"),
                    ("bid_vol_multiplier", "30.0"),
                    ("ask_vol_multiplier", "30.0"),
                    ("static_volume_threshold", "0"),
                    ("static_bid_vol_threshold", "0"),
                    ("static_ask_vol_threshold", "0"),
                    ("accumulation_threshold", "50000"),
                    ("distribution_threshold", "50000"),
                    ("wash_threshold", "50000"),
                    ("support_threshold", "50000"),
                    ("smash_threshold", "50000"),
                    ("use_simplified_thresholds", "1"),
                ]
            ),
        ),
    ]
)

_HEADER = (
    "# 本机配置，不进 Git。缺文件或缺键时由程序按缺省值补齐，已有值不覆盖。\n"
    "# 请用启动器补齐 [Account] account_id 与 [qmt_builtin] qmt_python_dir（不必手改本文件）。\n"
    "# builtin 下 path_qmt 可留空。\n\n"
)


def project_root(root: Optional[str] = None) -> str:
    if root:
        return os.path.abspath(root)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def config_ini_path(root: Optional[str] = None) -> str:
    return os.path.join(project_root(root), "data", "config.ini")


def _has_option(cfg: configparser.ConfigParser, section: str, key: str) -> bool:
    if section == "DEFAULT":
        return key in cfg.defaults()
    return cfg.has_option(section, key)


def _set_option(cfg: configparser.ConfigParser, section: str, key: str, value: str) -> None:
    if section == "DEFAULT":
        cfg.set("DEFAULT", key, value)
        return
    if not cfg.has_section(section):
        cfg.add_section(section)
    cfg.set(section, key, value)


def _write_ini(path: str, cfg: configparser.ConfigParser, *, with_header: bool) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if with_header:
            f.write(_HEADER)
        cfg.write(f)
    os.replace(tmp, path)


def update_config_values(updates: Mapping[Tuple[str, str], str], root: Optional[str] = None) -> str:
    """写入指定键，其它配置保持不变。"""
    ensure_app_config_ini(root)
    path = config_ini_path(root)
    cfg = configparser.ConfigParser()
    cfg.read(path, encoding="utf-8-sig")
    for (section, key), value in (updates or {}).items():
        _set_option(cfg, section, str(key), str(value if value is not None else ""))
    _write_ini(path, cfg, with_header=False)
    return path


def missing_qmt_setup(root: Optional[str] = None) -> Tuple[str, ...]:
    """缺资金账号或大 QMT python 目录时返回缺项名。"""
    ensure_app_config_ini(root)
    path = config_ini_path(root)
    cfg = configparser.ConfigParser()
    try:
        cfg.read(path, encoding="utf-8-sig")
    except Exception:
        return ("account_id", "qmt_python_dir")
    missing = []
    acc = ""
    py = ""
    if cfg.has_section("Account"):
        acc = str(cfg.get("Account", "account_id", fallback="") or "").strip()
    if cfg.has_section("qmt_builtin"):
        py = str(cfg.get("qmt_builtin", "qmt_python_dir", fallback="") or "").strip()
    if not acc:
        missing.append("account_id")
    if not py or not os.path.isdir(py):
        missing.append("qmt_python_dir")
    return tuple(missing)


def ensure_app_config_ini(root: Optional[str] = None) -> Tuple[str, bool, Tuple[str, ...]]:
    """确保 data/config.ini 存在且含全部缺省键。

    返回 (路径, 是否新建, 补上的 section.key 列表)。已有键不改。
    """
    path = config_ini_path(root)
    cfg = configparser.ConfigParser()
    created = not os.path.isfile(path) or os.path.getsize(path) <= 0
    if not created:
        try:
            cfg.read(path, encoding="utf-8-sig")
        except Exception:
            created = True
            cfg = configparser.ConfigParser()

    added = []
    for section, keys in DEFAULT_SECTIONS.items():
        for key, value in keys.items():
            if _has_option(cfg, section, key):
                continue
            _set_option(cfg, section, key, value)
            added.append("%s.%s" % (section, key))

    if created or added:
        _write_ini(path, cfg, with_header=created)
    return path, created, tuple(added)
