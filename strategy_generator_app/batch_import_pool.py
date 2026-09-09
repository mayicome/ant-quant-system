# -*- coding: utf-8 -*-
"""股票池：批量导入选股文件（设置持久化 + 在 history_data/存档中按前缀匹配）。"""
from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from PyQt5.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

_APP_DIR = os.path.dirname(os.path.abspath(__file__))
SETTINGS_PATH = os.path.join(_APP_DIR, "config", "batch_import_pool_settings.json")

_DATE_DASH = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
_DATE_COMPACT = re.compile(r"(20\d{2})(\d{2})(\d{2})")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "file_prefix": "选股结果_马总选股逻辑-盘后",
    "recent_trading_days": 5,
    "only_meet_condition": True,
    # 剔除：选股日后最高价相对选股日收盘涨幅超限
    "filter_post_select_max_gain": False,
    "main_board_max_gain_pct": 5.0,
    "other_board_max_gain_pct": 10.0,
}

_EXTS = (".xls", ".xlsx", ".csv")


def _clamp_gain_pct(value: Any, default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        v = float(default)
    return max(0.0, min(100.0, v))


def load_batch_import_settings() -> Dict[str, Any]:
    out = dict(DEFAULT_SETTINGS)
    if not os.path.isfile(SETTINGS_PATH):
        return out
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            if "file_prefix" in data:
                out["file_prefix"] = str(data.get("file_prefix") or "").strip()
            try:
                n = int(data.get("recent_trading_days") or out["recent_trading_days"])
                out["recent_trading_days"] = max(1, min(120, n))
            except (TypeError, ValueError):
                pass
            out["only_meet_condition"] = bool(data.get("only_meet_condition", True))
            out["filter_post_select_max_gain"] = bool(
                data.get("filter_post_select_max_gain", False)
            )
            out["main_board_max_gain_pct"] = _clamp_gain_pct(
                data.get("main_board_max_gain_pct"),
                float(DEFAULT_SETTINGS["main_board_max_gain_pct"]),
            )
            out["other_board_max_gain_pct"] = _clamp_gain_pct(
                data.get("other_board_max_gain_pct"),
                float(DEFAULT_SETTINGS["other_board_max_gain_pct"]),
            )
    except Exception:
        pass
    return out


def save_batch_import_settings(settings: Dict[str, Any]) -> None:
    payload = {
        "file_prefix": str(settings.get("file_prefix") or "").strip(),
        "recent_trading_days": max(
            1, min(120, int(settings.get("recent_trading_days") or 5))
        ),
        "only_meet_condition": bool(settings.get("only_meet_condition", True)),
        "filter_post_select_max_gain": bool(
            settings.get("filter_post_select_max_gain", False)
        ),
        "main_board_max_gain_pct": _clamp_gain_pct(
            settings.get("main_board_max_gain_pct"),
            float(DEFAULT_SETTINGS["main_board_max_gain_pct"]),
        ),
        "other_board_max_gain_pct": _clamp_gain_pct(
            settings.get("other_board_max_gain_pct"),
            float(DEFAULT_SETTINGS["other_board_max_gain_pct"]),
        ),
    }
    os.makedirs(os.path.dirname(SETTINGS_PATH), exist_ok=True)
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def _parse_ymd_token(y: str, m: str, d: str) -> Optional[date]:
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def dates_in_filename(filename: str) -> List[date]:
    """从文件名解析全部业务日期（去重保序）。"""
    name = os.path.basename(filename)
    found: List[date] = []
    seen: Set[date] = set()
    for m in _DATE_DASH.finditer(name):
        d = _parse_ymd_token(m.group(1), m.group(2), m.group(3))
        if d and d not in seen:
            seen.add(d)
            found.append(d)
    # 去掉已被 dash 匹配覆盖的紧凑串，避免重复
    compact_name = _DATE_DASH.sub(" ", name)
    for m in _DATE_COMPACT.finditer(compact_name):
        d = _parse_ymd_token(m.group(1), m.group(2), m.group(3))
        if d and d not in seen:
            seen.add(d)
            found.append(d)
    return found


def filename_covers_trading_days(filename: str, days: Sequence[date]) -> bool:
    """单日文件命中集合，或区间文件覆盖集合内任一日。"""
    day_set = set(days or [])
    if not day_set:
        return False
    parsed = dates_in_filename(filename)
    if not parsed:
        return False
    if any(d in day_set for d in parsed):
        return True
    if len(parsed) >= 2:
        lo, hi = min(parsed[0], parsed[1]), max(parsed[0], parsed[1])
        return any(lo <= d <= hi for d in day_set)
    return False


def _iter_history_search_dirs(history_dir: str) -> List[str]:
    """现行 history_data（含业务子目录）+ 存档目录。"""
    roots: List[str] = []
    if history_dir and os.path.isdir(history_dir):
        roots.append(history_dir)
    try:
        from utils.history_data_archive import archive_dir

        arch = archive_dir(history_dir)
    except Exception:
        arch = os.path.join(history_dir, "存档")
    if arch and os.path.isdir(arch) and arch not in roots:
        roots.append(arch)
    return roots


def find_selection_files_by_prefix(
    history_dir: str,
    prefix: str,
    trading_days: Sequence[date],
) -> List[str]:
    """在 history_data 与存档中递归查找：文件名以前缀开头，且日期匹配最近交易日。

    同名文件现行目录优先于存档；同路径只保留一次。
    """
    prefix = str(prefix or "").strip()
    if not prefix or not trading_days:
        return []

    # basename -> path（先扫现行，再存档，避免存档覆盖现行）
    by_name: Dict[str, str] = {}
    for root in _iter_history_search_dirs(history_dir):
        for dirpath, dirnames, filenames in os.walk(root):
            # 现行根下跳过「存档」，避免与单独扫存档重复
            if os.path.normcase(os.path.abspath(root)) == os.path.normcase(
                os.path.abspath(history_dir)
            ):
                dirnames[:] = [d for d in dirnames if d != "存档"]
            for fn in filenames:
                if fn.startswith("~$"):
                    continue
                ext = os.path.splitext(fn)[1].lower()
                if ext not in _EXTS:
                    continue
                if not fn.startswith(prefix):
                    continue
                if not filename_covers_trading_days(fn, trading_days):
                    continue
                full = os.path.join(dirpath, fn)
                # 现行优先：同名已有则不覆盖
                if fn not in by_name:
                    by_name[fn] = full

    paths = list(by_name.values())

    def _sort_key(p: str):
        ds = dates_in_filename(p)
        primary = max(ds) if ds else date.min
        return (primary, os.path.basename(p))

    paths.sort(key=_sort_key)
    return paths


def is_meet_condition_true(value: Any) -> bool:
    if value is True:
        return True
    if value is False or value is None:
        return False
    try:
        import pandas as pd

        if isinstance(value, float) and pd.isna(value):
            return False
    except Exception:
        pass
    if isinstance(value, (int, float)):
        try:
            return float(value) != 0.0
        except (TypeError, ValueError):
            return False
    s = str(value).strip().lower()
    if not s or s in ("nan", "none", "null", "-", "--"):
        return False
    return s in ("true", "1", "1.0", "是", "y", "yes", "t", "真", "满足")


def _norm_code6(code: Any) -> str:
    s = str(code or "").strip().upper()
    if "." in s:
        s = s.split(".", 1)[0]
    digits = "".join(c for c in s if c.isdigit())
    if not digits:
        return ""
    return digits.zfill(6)[-6:]


def _as_date(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    from datetime import datetime as _dt

    if isinstance(v, _dt):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()[:10]
    try:
        return date.fromisoformat(s)
    except Exception:
        return None


def _load_daily_for_gain(code6: str, through: date):
    """只读本地 daily_cache，避免 load_daily_dataframe 补洞卡 UI。"""
    try:
        from utils.daily_cache_reader import load_daily_from_cache

        return load_daily_from_cache(code6, through_date=through)
    except Exception:
        try:
            from daily_cache_reader import load_daily_from_cache as _ld  # type: ignore

            return _ld(code6, through_date=through)
        except Exception:
            return None


def post_select_max_gain_pct(
    stock_code: Any,
    selection_date: Any,
    *,
    through_date: Optional[date] = None,
) -> Optional[float]:
    """选股日后所有交易日最高价的最大值相对选股日收盘价的涨幅（百分比）。

    无日线 / 无选股日收盘 / 选股日后无有效最高价时返回 None。
    """
    c6 = _norm_code6(stock_code)
    sel = _as_date(selection_date)
    if not c6 or sel is None:
        return None
    through = _as_date(through_date) or date.today()
    if through < sel:
        return None

    df = _load_daily_for_gain(c6, through)
    if df is None or getattr(df, "empty", True):
        return None

    try:
        import pandas as pd

        dates = df["date"]
        closes = pd.to_numeric(df["close"], errors="coerce")
        highs = pd.to_numeric(df["high"], errors="coerce")
        sel_mask = dates == sel
        if not sel_mask.any():
            return None
        sel_close = float(closes.loc[sel_mask].iloc[-1])
        if not (sel_close > 0):
            return None
        after = (dates > sel) & highs.notna() & (highs > 0)
        if not after.any():
            return None
        max_high = float(highs.loc[after].max())
        if not (max_high > 0):
            return None
        return (max_high / sel_close - 1.0) * 100.0
    except Exception:
        return None


def max_gain_threshold_pct_for_code(
    stock_code: Any,
    *,
    main_board_pct: float = 5.0,
    other_board_pct: float = 10.0,
) -> float:
    try:
        from utils.limit_ratio import is_main_board
    except Exception:
        try:
            from limit_ratio import is_main_board  # type: ignore
        except Exception:
            is_main_board = None  # type: ignore
    c6 = _norm_code6(stock_code)
    main_thr = _clamp_gain_pct(main_board_pct, 5.0)
    other_thr = _clamp_gain_pct(other_board_pct, 10.0)
    if is_main_board is not None and c6 and is_main_board(c6):
        return main_thr
    return other_thr


def filter_by_day_post_select_max_gain(
    by_day: Dict[date, List[str]],
    *,
    main_board_pct: float = 5.0,
    other_board_pct: float = 10.0,
    through_date: Optional[date] = None,
    strength_by_day: Optional[Dict[date, Dict[str, Dict[str, object]]]] = None,
) -> Tuple[
    Dict[date, List[str]],
    Optional[Dict[date, Dict[str, Dict[str, object]]]],
    List[Dict[str, Any]],
]:
    """按选股日后最大涨幅过滤 by_day（及可选 strength）。

    返回 (过滤后 by_day, 过滤后 strength_by_day, 被剔除明细)。
    无法判定（缺日线等）的股票保留。
    """
    dropped: List[Dict[str, Any]] = []
    out_by: Dict[date, List[str]] = {}
    out_strength: Optional[Dict[date, Dict[str, Dict[str, object]]]] = (
        {} if strength_by_day is not None else None
    )
    through = _as_date(through_date) or date.today()

    for d, codes in (by_day or {}).items():
        sel = _as_date(d)
        kept: List[str] = []
        day_strength = (strength_by_day or {}).get(d) if strength_by_day else None
        kept_strength: Dict[str, Dict[str, object]] = {}
        for raw in codes or []:
            c6 = _norm_code6(raw)
            if not c6 or sel is None:
                if c6:
                    kept.append(c6)
                    if day_strength and c6 in day_strength and isinstance(
                        day_strength[c6], dict
                    ):
                        kept_strength[c6] = day_strength[c6]
                continue
            thr = max_gain_threshold_pct_for_code(
                c6, main_board_pct=main_board_pct, other_board_pct=other_board_pct
            )
            gain = post_select_max_gain_pct(c6, sel, through_date=through)
            if gain is not None and gain > thr:
                dropped.append(
                    {
                        "code": c6,
                        "selection_date": sel.isoformat(),
                        "max_gain_pct": round(gain, 4),
                        "threshold_pct": thr,
                    }
                )
                continue
            kept.append(c6)
            if day_strength and c6 in day_strength and isinstance(day_strength[c6], dict):
                kept_strength[c6] = day_strength[c6]
        if kept:
            out_by[d] = kept
            if out_strength is not None and kept_strength:
                out_strength[d] = kept_strength
    return out_by, out_strength, dropped


class BatchImportPoolSettingsDialog(QDialog):
    """设置：选股文件前缀、最近交易日数量、是否只导入满足条件、最大涨幅剔除。"""

    def __init__(self, parent=None, settings: Optional[Dict[str, Any]] = None):
        super().__init__(parent)
        self.setWindowTitle("批量导入选股文件 — 设置")
        self.setMinimumWidth(520)
        cur = dict(DEFAULT_SETTINGS)
        if isinstance(settings, dict):
            cur.update(settings)

        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.prefix_edit = QLineEdit()
        self.prefix_edit.setText(str(cur.get("file_prefix") or ""))
        self.prefix_edit.setPlaceholderText("例如：选股结果_马总选股逻辑-盘后")
        self.prefix_edit.setToolTip(
            "匹配 history_data（含业务子目录）与 history_data/存档 下\n"
            "文件名以此前缀开头的选股结果（.xls/.xlsx/.csv）。"
        )
        form.addRow("选股文件前缀：", self.prefix_edit)

        self.days_spin = QSpinBox()
        self.days_spin.setRange(1, 120)
        try:
            self.days_spin.setValue(int(cur.get("recent_trading_days") or 5))
        except (TypeError, ValueError):
            self.days_spin.setValue(5)
        self.days_spin.setToolTip("按最近 N 个交易日匹配文件名中的日期。")
        form.addRow("最近交易日数量：", self.days_spin)

        self.meet_cb = QCheckBox("只导入「满足条件」为 True 的股票")
        self.meet_cb.setChecked(bool(cur.get("only_meet_condition", True)))
        form.addRow("", self.meet_cb)

        self.gain_cb = QCheckBox(
            "剔除选股日后最大涨幅超限的股票（相对选股日收盘）"
        )
        self.gain_cb.setChecked(bool(cur.get("filter_post_select_max_gain", False)))
        self.gain_cb.setToolTip(
            "勾选后：用选股日后所有交易日最高价的最大值相对选股日收盘价算涨幅，\n"
            "超过主板/其他板块指定上限的股票不导入。缺日线无法判定时仍保留。"
        )
        form.addRow("", self.gain_cb)

        self.main_gain_spin = QDoubleSpinBox()
        self.main_gain_spin.setRange(0.0, 100.0)
        self.main_gain_spin.setDecimals(2)
        self.main_gain_spin.setSuffix(" %")
        self.main_gain_spin.setValue(
            _clamp_gain_pct(
                cur.get("main_board_max_gain_pct"),
                float(DEFAULT_SETTINGS["main_board_max_gain_pct"]),
            )
        )
        self.main_gain_spin.setToolTip("沪深主板（非创/科/北）允许的最大涨幅上限。")
        form.addRow("主板涨幅上限：", self.main_gain_spin)

        self.other_gain_spin = QDoubleSpinBox()
        self.other_gain_spin.setRange(0.0, 100.0)
        self.other_gain_spin.setDecimals(2)
        self.other_gain_spin.setSuffix(" %")
        self.other_gain_spin.setValue(
            _clamp_gain_pct(
                cur.get("other_board_max_gain_pct"),
                float(DEFAULT_SETTINGS["other_board_max_gain_pct"]),
            )
        )
        self.other_gain_spin.setToolTip(
            "创业板 / 科创板 / 北交所等非主板允许的最大涨幅上限。"
        )
        form.addRow("其他板块涨幅上限：", self.other_gain_spin)

        def _sync_gain_enabled(checked: bool = False) -> None:
            on = bool(self.gain_cb.isChecked())
            self.main_gain_spin.setEnabled(on)
            self.other_gain_spin.setEnabled(on)

        self.gain_cb.toggled.connect(_sync_gain_enabled)
        _sync_gain_enabled()

        layout.addLayout(form)

        tip = QLabel(
            "保存后立即生效。导入时在 history_data 与存档目录中按前缀+交易日找文件。"
        )
        tip.setWordWrap(True)
        tip.setStyleSheet("color: #666;")
        layout.addWidget(tip)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("保存")
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self._on_save)
        layout.addWidget(buttons)

    def _on_save(self) -> None:
        prefix = self.prefix_edit.text().strip()
        if not prefix:
            QMessageBox.warning(self, "设置", "请填写选股文件前缀。")
            return
        save_batch_import_settings(
            {
                "file_prefix": prefix,
                "recent_trading_days": int(self.days_spin.value()),
                "only_meet_condition": bool(self.meet_cb.isChecked()),
                "filter_post_select_max_gain": bool(self.gain_cb.isChecked()),
                "main_board_max_gain_pct": float(self.main_gain_spin.value()),
                "other_board_max_gain_pct": float(self.other_gain_spin.value()),
            }
        )
        self.accept()

    def result_settings(self) -> Dict[str, Any]:
        return load_batch_import_settings()


class BatchImportPoolDialog(QDialog):
    """入口弹窗：导入 / 设置。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("批量导入选股文件")
        self.setMinimumWidth(400)
        self.do_import = False

        layout = QVBoxLayout(self)
        self.summary_label = QLabel()
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)
        self._refresh_summary()

        row = QHBoxLayout()
        self.import_btn = QPushButton("导入")
        self.import_btn.setDefault(True)
        self.import_btn.clicked.connect(self._on_import)
        self.settings_btn = QPushButton("设置")
        self.settings_btn.clicked.connect(self._on_settings)
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        row.addWidget(self.import_btn)
        row.addWidget(self.settings_btn)
        row.addStretch()
        row.addWidget(cancel_btn)
        layout.addLayout(row)

    def _refresh_summary(self) -> None:
        s = load_batch_import_settings()
        meet = "是" if s.get("only_meet_condition") else "否"
        if s.get("filter_post_select_max_gain"):
            gain_txt = (
                f"是（主板≤{s.get('main_board_max_gain_pct')}%，"
                f"其他≤{s.get('other_board_max_gain_pct')}%）"
            )
        else:
            gain_txt = "否"
        self.summary_label.setText(
            f"前缀：{s.get('file_prefix') or '（未设置）'}\n"
            f"最近交易日：{s.get('recent_trading_days')} 天\n"
            f"只导入满足条件：{meet}\n"
            f"剔除选股日后最大涨幅超限：{gain_txt}\n\n"
            "选股文件查找范围：history_data（含子目录）与 history_data/存档。"
        )

    def _on_settings(self) -> None:
        dlg = BatchImportPoolSettingsDialog(self, load_batch_import_settings())
        if dlg.exec_() == QDialog.Accepted:
            self._refresh_summary()

    def _on_import(self) -> None:
        s = load_batch_import_settings()
        if not str(s.get("file_prefix") or "").strip():
            QMessageBox.warning(self, "导入", "请先在「设置」中填写选股文件前缀。")
            return
        self.do_import = True
        self.accept()
