# -*- coding: utf-8 -*-
"""Tick 本地缓存查看器（只读）。

数据根目录默认：<项目根>/data/ticks/{YYYYMMDD}/{6位代码}.parquet
（旧 .pkl 也可打开；主格式为 parquet，五档已展平。）

用法:
  python tools/tick_viewer.py
  python tools/tick_viewer.py --ticks-dir D:/蚂蚁量化系统/data/ticks
  python tools/tick_viewer.py --smoke
  python tools/tick_viewer.py --smoke --date 20260731 --code 000001
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from typing import List, Optional, Sequence, Tuple

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 超过该行数时弹提示（日频单票通常远小于此）
_LARGE_ROWS_WARN = 100_000

_TIME_COL_CANDIDATES = (
    "time_ts",
    "time",
    "datetime",
    "stime",
    "ticktime",
    "tick_time",
    "Time",
    "TIME",
)

# 典型 tick 列 → 中文表头（展示/导出用；数值不变）
_COLUMN_ZH = {
    "时间": "时间",
    "time_ts": "时间戳",
    "time": "时间戳",
    "datetime": "时间戳",
    "stime": "时间戳",
    "ticktime": "时间戳",
    "tick_time": "时间戳",
    "Time": "时间戳",
    "TIME": "时间戳",
    "lastPrice": "最新价",
    "open": "开盘",
    "high": "最高",
    "low": "最低",
    "lastClose": "昨收",
    "amount": "成交额",
    "volume": "成交量",
    "pvolume": "成交量(手)",
    "stockStatus": "证券状态",
    "openInt": "持仓量",
    "transactionNum": "成交笔数",
    "pe": "市盈率",
}
_COLUMN_ZH.update({f"ask{i}": f"卖{('一二三四五'[i - 1])}" for i in range(1, 6)})
_COLUMN_ZH.update({f"bid{i}": f"买{('一二三四五'[i - 1])}" for i in range(1, 6)})
_COLUMN_ZH.update({f"ask{i}_vol": f"卖{('一二三四五'[i - 1])}量" for i in range(1, 6)})
_COLUMN_ZH.update({f"bid{i}_vol": f"买{('一二三四五'[i - 1])}量" for i in range(1, 6)})
_COLUMN_ZH.update({f"askVol{i}": f"卖{('一二三四五'[i - 1])}量" for i in range(1, 6)})
_COLUMN_ZH.update({f"bidVol{i}": f"买{('一二三四五'[i - 1])}量" for i in range(1, 6)})


def project_root() -> str:
    return _ROOT


def default_ticks_dir() -> str:
    return os.path.join(project_root(), "data", "ticks")


def norm_code6(code: str) -> str:
    """000001.SZ / 000001 / 1 → 000001。"""
    s = (code or "").strip().upper()
    if not s:
        return ""
    m = re.search(r"(\d{6})", s)
    if m:
        return m.group(1)
    digits = re.sub(r"\D", "", s)
    if not digits:
        return ""
    return digits.zfill(6)[:6]


def list_date_dirs(ticks_dir: str) -> List[str]:
    if not os.path.isdir(ticks_dir):
        return []
    out = [
        name
        for name in os.listdir(ticks_dir)
        if len(name) == 8 and name.isdigit() and os.path.isdir(os.path.join(ticks_dir, name))
    ]
    return sorted(out)


def list_codes_for_date(ticks_dir: str, ymd: str) -> List[str]:
    day_dir = os.path.join(ticks_dir, ymd)
    if not os.path.isdir(day_dir):
        return []
    codes = set()
    for name in os.listdir(day_dir):
        if name.startswith("_"):
            continue
        base, ext = os.path.splitext(name)
        if len(base) != 6 or not base.isdigit():
            continue
        if ext.lower() not in (".parquet", ".pkl"):
            continue
        path = os.path.join(day_dir, name)
        try:
            if os.path.getsize(path) <= 32:
                continue
        except OSError:
            continue
        codes.add(base)
    return sorted(codes)


def resolve_tick_path(ticks_dir: str, ymd: str, code: str) -> Optional[str]:
    """优先 parquet，其次 pkl；文件名仅为 6 位代码。"""
    c6 = norm_code6(code)
    if not c6 or len(ymd) != 8:
        return None
    day_dir = os.path.join(ticks_dir, ymd)
    for ext in (".parquet", ".pkl"):
        path = os.path.join(day_dir, c6 + ext)
        try:
            if os.path.isfile(path) and os.path.getsize(path) > 32:
                return path
        except OSError:
            continue
    return None


def _looks_like_hhmmss_int(v: float) -> bool:
    """QMT 常见 HHMMSSmmm / HHMMSS 整数。"""
    if v != v:  # NaN
        return False
    iv = int(abs(v))
    if iv < 90000:  # 早于 09:00:00 不太像盘中 tick 整型
        return False
    if iv <= 235959:
        return True
    if 90000000 <= iv <= 235959999:  # HHMMSSmmm
        return True
    return False


def _fmt_hhmmss_int(v: int, trade_ymd: Optional[str] = None) -> str:
    s = str(abs(int(v)))
    ms = 0
    if len(s) >= 9:  # HHMMSSmmm
        hh, mm, ss, ms = int(s[:-7] or "0"), int(s[-7:-5]), int(s[-5:-3]), int(s[-3:])
    elif len(s) == 8:  # HMMSS + ? rare; treat as 0HHMMSSmmm truncated — fallback pad
        s = s.zfill(9)
        hh, mm, ss, ms = int(s[:-7]), int(s[-7:-5]), int(s[-5:-3]), int(s[-3:])
    else:  # HHMMSS
        s = s.zfill(6)
        hh, mm, ss = int(s[0:2]), int(s[2:4]), int(s[4:6])
    if trade_ymd and len(trade_ymd) == 8 and trade_ymd.isdigit():
        base = f"{trade_ymd[0:4]}-{trade_ymd[4:6]}-{trade_ymd[6:8]}"
    else:
        base = "0000-00-00"
    if ms:
        return f"{base} {hh:02d}:{mm:02d}:{ss:02d}.{ms:03d}"
    return f"{base} {hh:02d}:{mm:02d}:{ss:02d}"


def convert_time_series(
    series,
    trade_ymd: Optional[str] = None,
):
    """把常见 tick 时间列转为可读字符串 Series（保留原列，调用方决定插入位置）。"""
    import pandas as pd
    import numpy as np

    s = series
    if pd.api.types.is_datetime64_any_dtype(s):
        # 可能带 tz
        try:
            if getattr(s.dt, "tz", None) is not None:
                s = s.dt.tz_convert("Asia/Shanghai")
        except Exception:
            pass
        # 有毫秒则保留 3 位
        try:
            has_ms = bool((s.dt.microsecond.fillna(0) % 1000 != 0).any() or (s.dt.microsecond.fillna(0) // 1000 > 0).any())
        except Exception:
            has_ms = False
        if has_ms:
            return s.dt.strftime("%Y-%m-%d %H:%M:%S.%f").str[:-3]
        return s.dt.strftime("%Y-%m-%d %H:%M:%S")

    if pd.api.types.is_numeric_dtype(s):
        arr = pd.to_numeric(s, errors="coerce")
        sample = arr.dropna()
        if sample.empty:
            return s.astype(str)

        med = float(sample.median())
        # 微秒 epoch（先于毫秒判断，避免 1e14+ 误走 ms）
        if med > 1e14:
            dt = pd.to_datetime(arr, unit="us", utc=True, errors="coerce")
            try:
                dt = dt.dt.tz_convert("Asia/Shanghai")
            except Exception:
                pass
            return dt.dt.strftime("%Y-%m-%d %H:%M:%S.%f").str[:-3]

        # 毫秒 epoch（本项目 parquet 的 time_ts）
        if med > 1e11:
            dt = pd.to_datetime(arr, unit="ms", utc=True, errors="coerce")
            try:
                dt = dt.dt.tz_convert("Asia/Shanghai")
            except Exception:
                pass
            # QMT tick 多为整秒；有亚秒则带 ms
            try:
                has_ms = bool((dt.dt.microsecond.fillna(0) // 1000 > 0).any())
            except Exception:
                has_ms = False
            if has_ms:
                return dt.dt.strftime("%Y-%m-%d %H:%M:%S.%f").str[:-3]
            return dt.dt.strftime("%Y-%m-%d %H:%M:%S")

        # 秒 epoch
        if 1e9 < med < 1e11:
            dt = pd.to_datetime(arr, unit="s", utc=True, errors="coerce")
            try:
                dt = dt.dt.tz_convert("Asia/Shanghai")
            except Exception:
                pass
            return dt.dt.strftime("%Y-%m-%d %H:%M:%S")

        # HHMMSSmmm / HHMMSS
        probe = sample.iloc[0]
        if _looks_like_hhmmss_int(float(probe)):
            return arr.map(
                lambda x: _fmt_hhmmss_int(int(x), trade_ymd) if pd.notna(x) else ""
            )

    # 字符串：尽量 parse
    try:
        dt = pd.to_datetime(s, errors="coerce")
        if dt.notna().any():
            try:
                if getattr(dt.dt, "tz", None) is not None:
                    dt = dt.dt.tz_convert("Asia/Shanghai")
            except Exception:
                pass
            return dt.dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass
    return s.astype(str)


def add_readable_time_columns(df, trade_ymd: Optional[str] = None):
    """在原表前插入可读时间列；不覆盖原始数值列。"""
    import pandas as pd

    out = df.copy()
    inserted = []
    for col in _TIME_COL_CANDIDATES:
        if col not in out.columns:
            continue
        readable_name = "时间" if col in ("time_ts", "time") else f"{col}_可读"
        # 避免重复
        if readable_name in out.columns:
            readable_name = f"{col}_可读"
        try:
            out[readable_name] = convert_time_series(out[col], trade_ymd=trade_ymd)
            inserted.append(readable_name)
        except Exception:
            continue
        # 通常只需主时间列一份可读
        if col in ("time_ts", "time", "datetime"):
            break

    if inserted:
        cols = list(out.columns)
        front = [c for c in inserted if c in cols]
        rest = [c for c in cols if c not in front]
        out = out[front + rest]
    return out


def column_to_zh(col: str) -> str:
    """单列英文名 → 中文表头；未知列保持原名。"""
    s = str(col)
    if s in _COLUMN_ZH:
        return _COLUMN_ZH[s]
    if s.endswith("_可读"):
        return s  # 已是可读时间派生列
    return s


def to_display_dataframe(df):
    """生成表格/Excel 展示用 DataFrame：中文列名；有「时间」时隐藏原始 epoch 时间列。

    不修改单元格数值，仅改列名与可选列裁剪。
    """
    out = df.copy()
    if "时间" in out.columns:
        drop_cols = [c for c in out.columns if c in _TIME_COL_CANDIDATES]
        if drop_cols:
            out = out.drop(columns=drop_cols)

    rename = {}
    used = set()
    for c in out.columns:
        zh = column_to_zh(str(c))
        if zh in used and zh != str(c):
            zh = f"{zh}({c})"
        rename[c] = zh
        used.add(zh)
    return out.rename(columns=rename)


def load_tick_dataframe(path: str, trade_ymd: Optional[str] = None):
    """读取 parquet/pkl，并附加可读时间列。"""
    import pandas as pd

    ext = os.path.splitext(path)[1].lower()
    if ext == ".parquet":
        df = pd.read_parquet(path)
    elif ext == ".pkl":
        raw = pd.read_pickle(path)
        df = pd.DataFrame(raw) if not isinstance(raw, pd.DataFrame) else raw
    else:
        raise ValueError(f"不支持的文件类型: {ext}")

    if trade_ymd is None:
        # 从路径推断 YYYYMMDD
        parent = os.path.basename(os.path.dirname(os.path.abspath(path)))
        if len(parent) == 8 and parent.isdigit():
            trade_ymd = parent

    return add_readable_time_columns(df, trade_ymd=trade_ymd)


def _cell_text(v) -> str:
    import pandas as pd
    import math

    if v is None or (isinstance(v, float) and (math.isnan(v) or pd.isna(v))):
        return ""
    try:
        if pd.isna(v):
            return ""
    except Exception:
        pass
    if isinstance(v, float):
        # 价格等保留合理位数
        if abs(v) >= 1000 and v == int(v):
            return str(int(v))
        return f"{v:.6g}"
    return str(v)


def run_smoke(ticks_dir: str, date: Optional[str] = None, code: Optional[str] = None) -> int:
    dates = list_date_dirs(ticks_dir)
    if not dates:
        print(f"[smoke] 无日期目录: {ticks_dir}")
        return 1
    ymd = (date or "").strip().replace("-", "")[:8] or dates[-1]
    if ymd not in dates:
        print(f"[smoke] 日期 {ymd} 不存在，改用 {dates[-1]}")
        ymd = dates[-1]
    codes = list_codes_for_date(ticks_dir, ymd)
    if not codes:
        print(f"[smoke] {ymd} 下无股票文件")
        return 1
    c6 = norm_code6(code or "") or codes[0]
    path = resolve_tick_path(ticks_dir, ymd, c6)
    if not path:
        print(f"[smoke] 未找到文件: {ymd}/{c6}")
        return 1
    df = load_tick_dataframe(path, trade_ymd=ymd)
    display = to_display_dataframe(df)
    print(f"[smoke] path={path}")
    print(f"[smoke] rows={len(display)} cols={list(display.columns)}")
    print(display.head(5).to_string())
    return 0


def _build_gui(ticks_dir: str):
    from PyQt5.QtCore import Qt
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import (
        QApplication,
        QComboBox,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QPushButton,
        QStatusBar,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
        QHeaderView,
        QAbstractItemView,
    )

    class TickViewerWindow(QMainWindow):
        def __init__(self, ticks_root: str):
            super().__init__()
            self.ticks_dir = ticks_root
            self._df = None  # type: ignore
            self._path = ""
            self.setWindowTitle("Tick 查看器")
            self.resize(1180, 720)
            self._build_ui()
            self._reload_dates()

        def _build_ui(self) -> None:
            root = QWidget(self)
            self.setCentralWidget(root)
            layout = QVBoxLayout(root)

            top = QHBoxLayout()
            top.addWidget(QLabel("日期:"))
            self.cmb_date = QComboBox()
            self.cmb_date.setMinimumWidth(120)
            self.cmb_date.currentTextChanged.connect(self._on_date_changed)
            top.addWidget(self.cmb_date)

            top.addWidget(QLabel("股票:"))
            self.cmb_code = QComboBox()
            self.cmb_code.setEditable(True)
            self.cmb_code.setMinimumWidth(160)
            self.cmb_code.setInsertPolicy(QComboBox.NoInsert)
            self.cmb_code.currentIndexChanged.connect(self._on_stock_changed)
            self.cmb_code.editTextChanged.connect(self._on_stock_changed)
            top.addWidget(self.cmb_code)

            top.addWidget(QLabel("或输入代码:"))
            self.edt_code = QLineEdit()
            self.edt_code.setPlaceholderText("000001 或 000001.SZ")
            self.edt_code.setMaximumWidth(140)
            self.edt_code.textChanged.connect(self._on_stock_changed)
            self.edt_code.returnPressed.connect(self._load)
            top.addWidget(self.edt_code)

            self.btn_load = QPushButton("加载")
            self.btn_load.clicked.connect(self._load)
            top.addWidget(self.btn_load)

            self.btn_export = QPushButton("导出Excel")
            self.btn_export.clicked.connect(self._export)
            self.btn_export.setEnabled(False)
            top.addWidget(self.btn_export)

            self.btn_refresh = QPushButton("刷新日期")
            self.btn_refresh.clicked.connect(self._reload_dates)
            top.addWidget(self.btn_refresh)

            top.addStretch(1)
            layout.addLayout(top)

            path_row = QHBoxLayout()
            path_row.addWidget(QLabel("目录:"))
            self.lbl_dir = QLabel(self.ticks_dir)
            self.lbl_dir.setTextInteractionFlags(Qt.TextSelectableByMouse)
            path_row.addWidget(self.lbl_dir, 1)
            layout.addLayout(path_row)

            self.table = QTableWidget()
            self.table.setAlternatingRowColors(True)
            self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
            self.table.horizontalHeader().setStretchLastSection(True)
            font = QFont("Microsoft YaHei", 9)
            self.table.setFont(font)
            layout.addWidget(self.table, 1)

            self.setStatusBar(QStatusBar())
            self.statusBar().showMessage(f"就绪 | ticks={self.ticks_dir}")

        def _reload_dates(self) -> None:
            dates = list_date_dirs(self.ticks_dir)
            cur = self.cmb_date.currentText()
            self.cmb_date.blockSignals(True)
            self.cmb_date.clear()
            self.cmb_date.addItems(dates)
            self.cmb_date.blockSignals(False)
            if not dates:
                self.cmb_code.blockSignals(True)
                self.cmb_code.clear()
                self.cmb_code.blockSignals(False)
                self._clear_tick_view(status=f"未找到日期目录: {self.ticks_dir}")
                return
            if cur in dates:
                self.cmb_date.setCurrentText(cur)
            else:
                self.cmb_date.setCurrentIndex(len(dates) - 1)
            self._on_date_changed(self.cmb_date.currentText())

        def _clear_tick_view(self, status: Optional[str] = None) -> None:
            """清空已加载表格与路径状态，避免切换日期/代码后仍显示旧数据。"""
            self._df = None
            self._path = ""
            self.btn_export.setEnabled(False)
            self.table.clear()
            self.table.setRowCount(0)
            self.table.setColumnCount(0)
            if status is not None:
                self.statusBar().showMessage(status)

        def _on_stock_changed(self, *_args) -> None:
            # 无已加载内容时不刷状态栏，避免初始化/程序性更新噪音
            if self._df is None and not self._path:
                return
            ymd = self.cmb_date.currentText().strip()
            self._clear_tick_view(
                status=f"已清空 | 日期 {ymd} | 请重新点击加载 | {self.ticks_dir}"
            )

        def _on_date_changed(self, ymd: str) -> None:
            ymd = (ymd or "").strip()
            self._clear_tick_view()
            codes = list_codes_for_date(self.ticks_dir, ymd) if ymd else []
            prev = self.cmb_code.currentText().strip()
            self.cmb_code.blockSignals(True)
            self.cmb_code.clear()
            self.cmb_code.addItems(codes)
            self.cmb_code.blockSignals(False)
            if prev:
                c6 = norm_code6(prev)
                idx = self.cmb_code.findText(c6)
                if idx >= 0:
                    self.cmb_code.setCurrentIndex(idx)
            self.statusBar().showMessage(
                f"日期 {ymd} | 股票数 {len(codes)} | {self.ticks_dir}"
            )

        def _current_code(self) -> str:
            typed = self.edt_code.text().strip()
            if typed:
                return typed
            return self.cmb_code.currentText().strip()

        def _load(self) -> None:
            ymd = self.cmb_date.currentText().strip()
            code = self._current_code()
            c6 = norm_code6(code)
            if not ymd:
                QMessageBox.warning(self, "提示", "请先选择日期")
                return
            if not c6:
                QMessageBox.warning(self, "提示", "请选择或输入股票代码")
                return

            path = resolve_tick_path(self.ticks_dir, ymd, c6)
            if not path:
                QMessageBox.warning(
                    self,
                    "未找到",
                    f"未找到文件:\n{os.path.join(self.ticks_dir, ymd, c6 + '.parquet')}\n"
                    f"（也尝试了 .pkl）",
                )
                return

            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                df = load_tick_dataframe(path, trade_ymd=ymd)
            except Exception as e:
                QApplication.restoreOverrideCursor()
                QMessageBox.critical(self, "加载失败", str(e))
                return
            QApplication.restoreOverrideCursor()

            n = len(df)
            if n > _LARGE_ROWS_WARN:
                ret = QMessageBox.question(
                    self,
                    "行数较多",
                    f"共 {n} 行（>{_LARGE_ROWS_WARN}），全部载入表格可能较慢。\n是否继续？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes,
                )
                if ret != QMessageBox.Yes:
                    self.statusBar().showMessage(f"已取消加载: {path} ({n} 行)")
                    return

            self._df = df
            self._path = path
            self._fill_table(df)
            self.btn_export.setEnabled(True)
            # 同步下拉/输入（阻断信号，避免加载后立刻被清空）
            self.cmb_code.blockSignals(True)
            self.edt_code.blockSignals(True)
            try:
                idx = self.cmb_code.findText(c6)
                if idx >= 0:
                    self.cmb_code.setCurrentIndex(idx)
                self.edt_code.setText(c6)
            finally:
                self.cmb_code.blockSignals(False)
                self.edt_code.blockSignals(False)
            n_cols = len(to_display_dataframe(df).columns)
            self.statusBar().showMessage(
                f"已加载 {c6} @ {ymd} | {n} 行 × {n_cols} 列 | {path}"
            )

        def _fill_table(self, df) -> None:
            display = to_display_dataframe(df)
            cols = [str(c) for c in display.columns]
            self.table.clear()
            self.table.setColumnCount(len(cols))
            self.table.setHorizontalHeaderLabels(cols)
            self.table.setRowCount(len(display))
            # 分块写入，偶发 processEvents 保持响应
            values = display.itertuples(index=False, name=None)
            for r, row in enumerate(values):
                for c, v in enumerate(row):
                    self.table.setItem(r, c, QTableWidgetItem(_cell_text(v)))
                if r > 0 and r % 2000 == 0:
                    QApplication.processEvents()
            self.table.resizeColumnsToContents()

        def _export(self) -> None:
            if self._df is None or self._df.empty:
                QMessageBox.information(self, "提示", "请先加载数据")
                return
            ymd = self.cmb_date.currentText().strip() or "ticks"
            c6 = norm_code6(self._current_code()) or "code"
            default_name = f"ticks_{ymd}_{c6}.xlsx"
            # 默认：用户下载目录，其次 ticks 旁
            downloads = os.path.join(os.path.expanduser("~"), "Downloads")
            if not os.path.isdir(downloads):
                downloads = os.path.dirname(self._path) if self._path else self.ticks_dir
            start = os.path.join(downloads, default_name)
            path, _ = QFileDialog.getSaveFileName(
                self, "导出 Excel", start, "Excel (*.xlsx)"
            )
            if not path:
                return
            if not path.lower().endswith(".xlsx"):
                path += ".xlsx"
            QApplication.setOverrideCursor(Qt.WaitCursor)
            try:
                # 与表格同一套中文列名展示帧
                to_display_dataframe(self._df).to_excel(
                    path, index=False, engine="openpyxl"
                )
            except Exception as e:
                QApplication.restoreOverrideCursor()
                QMessageBox.critical(self, "导出失败", str(e))
                return
            QApplication.restoreOverrideCursor()
            self.statusBar().showMessage(f"已导出: {path}")
            QMessageBox.information(self, "完成", f"已导出:\n{path}")

    return TickViewerWindow


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Tick parquet/pkl 查看器")
    parser.add_argument(
        "--ticks-dir",
        default=None,
        help="ticks 根目录（默认 <项目根>/data/ticks；仅 CLI/冒烟用，GUI 无更换入口）",
    )
    parser.add_argument("--smoke", action="store_true", help="无 GUI 冒烟：加载并打印 head")
    parser.add_argument("--date", default=None, help="冒烟用日期 YYYYMMDD")
    parser.add_argument("--code", default=None, help="冒烟用股票代码")
    args = parser.parse_args(list(argv) if argv is not None else None)

    ticks_dir = os.path.abspath(args.ticks_dir or default_ticks_dir())

    if args.smoke:
        return run_smoke(ticks_dir, date=args.date, code=args.code)

    try:
        from PyQt5.QtWidgets import QApplication
    except ImportError:
        print("需要 PyQt5：pip install PyQt5", file=sys.stderr)
        return 1

    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    Win = _build_gui(ticks_dir)
    win = Win(ticks_dir)
    win.show()
    return int(app.exec_())


if __name__ == "__main__":
    raise SystemExit(main())
