# -*- coding: utf-8 -*-
"""
四个监控 GUI 共用的「股票类型」筛选配置。

勾选 = 参与统计展示；取消勾选 = 排除。
板类型按代码前缀；ST 另按名称判定（仍须所属板块也被勾选）。

配置文件: data/monitor_stock_type_filter.json
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "data" / "monitor_stock_type_filter.json"

# key → (界面标签, 说明)
TYPE_DEFS: Dict[str, tuple[str, str]] = {
    "st": ("ST股", "名称含 ST / *ST（所属板块仍须勾选）"),
    "sh_main": ("沪市主板", "代码 60 开头"),
    "sz_main": ("深市主板", "代码 00 开头"),
    "chinext": ("创业板", "代码 30 开头（含 300/301）"),
    "star": ("科创板", "代码 688/689 开头"),
    "bj": ("北交所", "代码 92/8/4 开头"),
}

DEFAULT_CFG: Dict[str, bool] = {k: True for k in TYPE_DEFS}


def _repo_root() -> Path:
    return ROOT


def config_path() -> Path:
    return _repo_root() / "data" / "monitor_stock_type_filter.json"


def default_config() -> Dict[str, bool]:
    return dict(DEFAULT_CFG)


def load_config() -> Dict[str, bool]:
    cfg = default_config()
    path = config_path()
    if not path.is_file():
        return cfg
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return cfg
    if not isinstance(raw, dict):
        return cfg
    for k in TYPE_DEFS:
        if k in raw:
            cfg[k] = bool(raw[k])
    return cfg


def save_config(cfg: Dict[str, bool]) -> Path:
    out = default_config()
    for k in TYPE_DEFS:
        if k in cfg:
            out[k] = bool(cfg[k])
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def code6(v: Any) -> str:
    try:
        return str(int(float(v))).zfill(6)
    except Exception:
        s = "".join(c for c in str(v or "") if c.isdigit())
        return s.zfill(6)[-6:] if s else ""


def is_st_name(name: Any) -> bool:
    return "ST" in str(name or "").upper()


def board_key(code: Any) -> Optional[str]:
    """代码 → 板类型 key；无法识别返回 None。"""
    c = code6(code)
    if not c or len(c) < 3:
        return None
    if c.startswith("60"):
        return "sh_main"
    if c.startswith("00"):
        return "sz_main"
    if c.startswith("30"):
        return "chinext"
    if c.startswith(("688", "689")):
        return "star"
    if c.startswith("92") or c.startswith(("8", "4")):
        # 8xxxxx / 4xxxxx 为旧北交所；92xxxx 为新北交所
        if c.startswith("92") or c[0] in ("8", "4"):
            return "bj"
    return None


def allow_code_name(code: Any, name: Any = "", *, cfg: Optional[Dict[str, bool]] = None) -> bool:
    """单票是否纳入统计。"""
    c = cfg or load_config()
    bk = board_key(code)
    if bk is None:
        return False
    if not c.get(bk, True):
        return False
    if is_st_name(name) and not c.get("st", True):
        return False
    return True


def summarize_config(cfg: Optional[Dict[str, bool]] = None) -> str:
    c = cfg or load_config()
    on = [TYPE_DEFS[k][0] for k in TYPE_DEFS if c.get(k, True)]
    off = [TYPE_DEFS[k][0] for k in TYPE_DEFS if not c.get(k, True)]
    if not off:
        return "股票类型: 全部"
    if not on:
        return "股票类型: 无（全部排除）"
    return "排除: " + "、".join(off)


def filter_dataframe(
    df: pd.DataFrame,
    *,
    code_col: str = "code",
    name_col: Optional[str] = "name",
    cfg: Optional[Dict[str, bool]] = None,
) -> pd.DataFrame:
    """按当前配置过滤 DataFrame；缺列则尽量兼容。"""
    if df is None or getattr(df, "empty", True):
        return df
    c = cfg or load_config()
    # 全开则无需过滤
    if all(c.get(k, True) for k in TYPE_DEFS):
        return df

    out = df.copy()
    if code_col not in out.columns:
        # 选股文件常用「股票代码」/「代码」
        for alt in ("股票代码", "代码"):
            if alt in out.columns:
                code_col = alt
                break
        else:
            return out
    if name_col is None or name_col not in out.columns:
        for alt in ("name", "股票名称", "名称"):
            if alt in out.columns:
                name_col = alt
                break
        else:
            name_col = None

    codes = out[code_col].map(code6)
    if name_col:
        names = out[name_col]
    else:
        names = pd.Series([""] * len(out), index=out.index)

    mask = [
        allow_code_name(cd, nm, cfg=c)
        for cd, nm in zip(codes.tolist(), names.tolist())
    ]
    return out.loc[mask].copy()


def apply_to_pool(df: pd.DataFrame) -> pd.DataFrame:
    """监控 load_pool 出口调用。"""
    return filter_dataframe(df, code_col="code", name_col="name")


# ---------------------------------------------------------------------------
# GUI 对话框（四个监控共用）
# ---------------------------------------------------------------------------

def open_stock_type_dialog(parent=None) -> bool:
    """弹出配置对话框。返回 True 表示用户点了确定且可能已改配置。"""
    from PyQt5.QtCore import Qt
    from PyQt5.QtWidgets import (
        QCheckBox,
        QDialog,
        QDialogButtonBox,
        QHBoxLayout,
        QLabel,
        QMessageBox,
        QPushButton,
        QVBoxLayout,
    )

    dlg = QDialog(parent)
    dlg.setWindowTitle("股票类型筛选（四个监控共用）")
    dlg.setMinimumWidth(360)
    layout = QVBoxLayout(dlg)

    tip = QLabel(
        "勾选 = 参与统计展示；取消 = 排除。\n"
        "配置写入 data/monitor_stock_type_filter.json，四个监控共用。\n"
        "ST 股须同时勾选「ST股」及其所属板块。"
    )
    tip.setWordWrap(True)
    tip.setStyleSheet("color: #555;")
    layout.addWidget(tip)

    cfg = load_config()
    boxes: Dict[str, QCheckBox] = {}
    for key, (label, hint) in TYPE_DEFS.items():
        cb = QCheckBox(label)
        cb.setChecked(bool(cfg.get(key, True)))
        cb.setToolTip(hint)
        layout.addWidget(cb)
        boxes[key] = cb

    row = QHBoxLayout()
    btn_all = QPushButton("全选")
    btn_none = QPushButton("全不选")
    row.addWidget(btn_all)
    row.addWidget(btn_none)
    row.addStretch(1)
    layout.addLayout(row)

    def _set_all(v: bool):
        for cb in boxes.values():
            cb.setChecked(v)

    btn_all.clicked.connect(lambda: _set_all(True))
    btn_none.clicked.connect(lambda: _set_all(False))

    buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    buttons.button(QDialogButtonBox.Ok).setText("确定")
    buttons.button(QDialogButtonBox.Cancel).setText("取消")
    layout.addWidget(buttons)
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)

    if dlg.exec_() != QDialog.Accepted:
        return False

    new_cfg = {k: boxes[k].isChecked() for k in TYPE_DEFS}
    if not any(new_cfg.values()):
        QMessageBox.warning(
            parent,
            "股票类型",
            "至少勾选一项，否则统计将为空。已取消保存。",
        )
        return False
    save_config(new_cfg)
    return True
