#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
布林%b回落选股 · 买点A/B对比监控 GUI

- 三个图叠画两组：买点A / 买点B（同一回测池按「买点」列拆分）
- 买点A=深度超跌反弹；买点B=下轨附近企稳
- 近窗滚动、开买日票均、累计开买日票均；量柱展示各组选股只数与成交笔数
- 点击某开买日 → 两组对比摘要 + 票明细
- 「更新选股+回测」：缺口补选股 + bb_pctb 买/卖回测（持有最多12日）

用法:
  python bb_pctb_pullback_monitor_gui.py
  python bb_pctb_pullback_monitor_gui.py --auto-run
  python bb_pctb_pullback_monitor_gui.py --window 10 --months 2
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from PyQt5.QtCore import QThread, Qt, QTimer, QSize, pyqtSignal
from PyQt5.QtGui import QCursor, QFont, QKeySequence
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSpinBox,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

warnings.filterwarnings("ignore")

import matplotlib

matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

MONITOR_WIDTH = 1080
MONITOR_HEIGHT = 620


class FlexibleFigureCanvas(FigureCanvas):
    """随容器伸缩；不把 figure 像素反推为窗口最小宽度。"""

    def minimumSizeHint(self):
        return QSize(0, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sync_figure_size()

    def sync_figure_size(self):
        w = max(int(self.width()), 1)
        h = max(int(self.height()), 1)
        dpi = self.figure.dpi
        self.figure.set_size_inches(w / dpi, h / dpi, forward=True)

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))

import bb_pctb_pullback_monitor as zb  # noqa: E402

plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

# 买点A / 买点B 配色
C_TB = "#2E86AB"
C_NO = "#E67E22"
C_TB_WIN = "#5DADE2"
C_NO_WIN = "#F5B041"


def _fmt_pct(v: Any) -> str:
    if v is None:
        return "NA"
    try:
        if pd.isna(v):
            return "NA"
    except Exception:
        pass
    if isinstance(v, float) and (np.isnan(v) or np.isinf(v)):
        return "NA"
    try:
        return f"{float(v):+.2f}%"
    except Exception:
        return "NA"


def _serialize_report(rep: dict) -> dict:
    day_view = rep.get("day_view")
    roll_view = rep.get("roll_view")
    cum_view = rep.get("cum_view")
    trigger = rep.get("trigger")
    df = rep.get("df")
    last_close = rep.get("last_close")
    return {
        "variant": rep.get("variant") or "",
        "trade_dir": rep.get("trade_dir") or "",
        "empty_reason": rep.get("empty_reason") or "",
        "legacy_buy_point": bool(rep.get("legacy_buy_point")),
        "legacy_buy_point": bool(rep.get("legacy_buy_point")),
        "files": rep.get("files") or [],
        "data_from": rep.get("data_from") or "",
        "data_to": rep.get("data_to") or "",
        "last_close": str(last_close) if last_close is not None else "",
        "n_start_days": rep.get("n_start_days") or 0,
        "window": rep.get("window"),
        "months": rep.get("months"),
        "full": rep.get("full") or {},
        "latest_roll": {
            k: (str(v) if isinstance(v, date) else v)
            for k, v in (rep.get("latest_roll") or {}).items()
        }
        if rep.get("latest_roll")
        else None,
        "day_view": day_view.to_dict(orient="list")
        if day_view is not None and hasattr(day_view, "to_dict")
        else {},
        "roll_view": roll_view.to_dict(orient="list")
        if roll_view is not None and hasattr(roll_view, "to_dict")
        else {},
        "cum_view": cum_view.to_dict(orient="list")
        if cum_view is not None and hasattr(cum_view, "to_dict")
        else {},
        "trigger": trigger.to_dict(orient="list")
        if trigger is not None and hasattr(trigger, "to_dict")
        else {},
        "sel_counts": (rep.get("sel_counts").to_dict(orient="list")
        if rep.get("sel_counts") is not None and hasattr(rep.get("sel_counts"), "to_dict")
        else {}),
        "starts": [str(x) for x in (rep.get("starts") or [])],
        "trades": _trades_by_start(df, last_close)
        if df is not None and hasattr(df, "empty") and not df.empty
        else {},
    }


def _df_from_dict(d: Any) -> pd.DataFrame:
    if not d:
        return pd.DataFrame()
    return pd.DataFrame(d)


def _series_by_start(df: pd.DataFrame, date_col: str, value_col: str) -> Dict[date, float]:
    if df is None or df.empty or date_col not in df.columns or value_col not in df.columns:
        return {}
    t = df.copy()
    t[date_col] = pd.to_datetime(t[date_col], errors="coerce").dt.date
    t[value_col] = pd.to_numeric(t[value_col], errors="coerce")
    t = t.dropna(subset=[date_col])
    return {r[date_col]: float(r[value_col]) if pd.notna(r[value_col]) else np.nan for _, r in t.iterrows()}


def _sel_count_map(reports: Dict[str, dict], variant: str) -> Dict[date, int]:
    """选股日 → 该分组入选只数。"""
    rep = reports.get(variant) or {}
    sc = _df_from_dict(rep.get("sel_counts"))
    if sc is None or sc.empty or "sel" not in sc.columns or "n_sel" not in sc.columns:
        return {}
    t = sc.copy()
    t["sel"] = pd.to_datetime(t["sel"], errors="coerce").dt.date
    t["n_sel"] = pd.to_numeric(t["n_sel"], errors="coerce")
    t = t.dropna(subset=["sel", "n_sel"])
    return {r["sel"]: int(r["n_sel"]) for _, r in t.iterrows()}


def _sel_count_map_legacy(reports: Dict[str, dict]) -> Dict[date, int]:
    """选股日 → 入选只数（兼容旧逻辑）。"""
    for v in zb.VARIANTS:
        rep = reports.get(v) or {}
        sc = _df_from_dict(rep.get("sel_counts"))
        if sc is None or sc.empty or "sel" not in sc.columns or "n_sel" not in sc.columns:
            continue
        t = sc.copy()
        t["sel"] = pd.to_datetime(t["sel"], errors="coerce").dt.date
        t["n_sel"] = pd.to_numeric(t["n_sel"], errors="coerce")
        t = t.dropna(subset=["sel", "n_sel"])
        return {r["sel"]: int(r["n_sel"]) for _, r in t.iterrows()}
    return {}


def _n_sel_series_for_asofs(
    asofs: List[date], reports: Dict[str, dict], variant: str
) -> np.ndarray:
    """每个开买日对应的「前一交易日该分组选股只数」。"""
    sel_map = _sel_count_map(reports, variant)
    # 兜底：从两侧 day_view 合并（旧序列化无 sel_counts 时）
    day_maps = []
    for v in zb.VARIANTS:
        day = _df_from_dict((reports.get(v) or {}).get("day_view"))
        day_maps.append(_series_by_start(day, "start", "n_sel"))

    out: List[float] = []
    for a in asofs:
        v = np.nan
        if sel_map:
            prev = zb.prev_trading_day(a)
            if prev is not None and prev in sel_map:
                v = float(sel_map[prev])
        if v != v:  # nan
            for m in day_maps:
                if a in m and m[a] == m[a]:
                    v = float(m[a])
                    break
        out.append(v)
    return np.array(out, dtype=float)


def _sample_starts(reports: Dict[str, dict]) -> List[date]:
    """月份窗口内、两侧有已了结开买样本的开买日。"""
    out: set = set()
    for v in zb.VARIANTS:
        rep = reports.get(v) or {}
        day = _df_from_dict(rep.get("day_view"))
        if not day.empty and "start" in day.columns:
            for s in day["start"].tolist():
                try:
                    d = pd.Timestamp(s).date()
                except Exception:
                    continue
                if d is not None and not pd.isna(d):
                    out.add(d)
            continue
        for s in rep.get("starts") or []:
            try:
                out.add(pd.Timestamp(s).date())
            except Exception:
                pass
    return sorted(out)


def _union_starts(reports: Dict[str, dict]) -> List[date]:
    """横轴开买日：月份窗口内每个交易日都占位（两侧都无样本也显示，曲线为 NaN）。"""
    samples = _sample_starts(reports)
    last_close: Optional[date] = None
    months = 2
    for v in zb.VARIANTS:
        rep = reports.get(v) or {}
        lc = rep.get("last_close")
        if lc is not None:
            try:
                d = lc if isinstance(lc, date) else pd.Timestamp(lc).date()
            except Exception:
                d = None
            if d is not None and (last_close is None or d > last_close):
                last_close = d
        if rep.get("months") is not None:
            try:
                months = int(rep["months"])
            except Exception:
                pass
    if last_close is None:
        return samples
    cut = last_close - timedelta(days=int(months * 31))
    return zb.trading_days_inclusive(cut, last_close, extra=samples)


class MonitorWorker(QThread):
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, window: int, months: int, parent=None):
        super().__init__(parent)
        self.window = int(window)
        self.months = int(months)

    def run(self):
        try:
            self.progress.emit("加载按票回测（必要时从成交明细重建）…")
            raw = zb.build_all_reports(window=self.window, months=self.months)
            reports = {v: _serialize_report(rep) for v, rep in raw.items()}
            legacy = bool((reports.get(zb.VARIANT_A) or {}).get("legacy_buy_point"))
            if not any(reports[v].get("starts") for v in zb.VARIANTS):
                has_fills = (
                    zb._latest_fill_csv("买入") is not None
                    and zb._latest_fill_csv("卖出") is not None
                )
                hint = (
                    "目录有买卖成交明细但无法汇总为按票收益。\n"
                    if has_fills
                    else f"请检查: {zb.DIR} 下是否有按票回测文件。\n"
                )
                raise RuntimeError(
                    "两组回测均无已了结开买日样本。\n"
                    + hint
                    + "可先点「更新选股+回测」补全数据。"
                )
            if legacy:
                reports["_legacy_buy_point"] = True
            self.finished_ok.emit({"reports": reports})
        except Exception as e:
            self.failed.emit(str(e))


def _trades_by_start(df: pd.DataFrame, asof) -> Dict[str, List[dict]]:
    d = zb.ensure_start(df)
    d = d[zb.pit_mask(d, asof)].copy()
    out: Dict[str, List[dict]] = {}
    for start, g in d.groupby("start"):
        rows = []
        for _, r in g.iterrows():
            known = r.get("known_on")
            try:
                known_s = str(pd.Timestamp(known).date()) if pd.notna(known) else ""
            except Exception:
                known_s = ""
            rows.append(
                {
                    "code": str(r.get("code") or ""),
                    "name": str(r.get("name") or ""),
                    "sel": str(r.get("sel") or ""),
                    "start": str(start),
                    "ret": None if pd.isna(r.get("ret")) else float(r["ret"]),
                    "known_on": known_s,
                    "note": str(r.get("备注") or ""),
                    "px": None
                    if ("买入成交价" not in r.index or pd.isna(r.get("买入成交价")))
                    else float(r["买入成交价"]),
                    "lu_day": None
                    if ("lu_day" not in r.index or pd.isna(r.get("lu_day")))
                    else float(r["lu_day"]),
                    "pctb": None
                    if ("pctb" not in r.index or pd.isna(r.get("pctb")))
                    else float(r["pctb"]),
                    "fail_reason": str(r.get("fail_reason") or ""),
                }
            )
        rows.sort(key=lambda x: -(x["ret"] if x["ret"] is not None else -999))
        out[str(start)] = rows
    return out


class PrepareDataWorker(QThread):
    """缺口补选股 + bb_pctb 买/卖回测（子进程）。"""

    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, max_days: int = 15, parent=None):
        super().__init__(parent)
        self.max_days = int(max_days)

    def run(self):
        try:
            script = ROOT / "tools" / "prepare_bb_pctb_pullback_data.py"
            if not script.is_file():
                raise FileNotFoundError(f"缺少脚本: {script}")
            # --no-reuse：改买点规则后必须重选
            cmd = [
                sys.executable,
                "-X",
                "utf8",
                str(script),
                "--days",
                str(self.max_days),
                "--no-reuse",
            ]
            self.progress.emit(
                f"开始准备数据（强制重选 + bb_pctb 回测，上限 {self.max_days} 天）…"
            )
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            proc = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            assert proc.stdout is not None
            last_lines: List[str] = []
            skipped = False
            for line in proc.stdout:
                s = line.rstrip()
                if not s:
                    continue
                last_lines.append(s)
                if len(last_lines) > 40:
                    last_lines = last_lines[-40:]
                if "无需补数" in s:
                    skipped = True
                self.progress.emit(s)
            rc = proc.wait()
            if rc != 0:
                tail = "\n".join(last_lines[-10:])
                raise RuntimeError(f"准备失败（退出码 {rc}）\n{tail}")
            if skipped:
                self.finished_ok.emit("无需补数：回测选股日已覆盖到最新收盘日，可直接「刷新」")
            else:
                self.finished_ok.emit(
                    "选股+回测完成（布林%b回落选股），可点「刷新」看图"
                )
        except Exception as e:
            self.failed.emit(str(e))


class CompareChartWidget(QWidget):
    """三个图，每个叠画买点A / 买点B。"""
    _FIG_W = 10.1
    _FIG_H = 5.5

    point_clicked = pyqtSignal(str)
    export_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.figure = Figure(figsize=(self._FIG_W, self._FIG_H), dpi=100)
        self.canvas = FlexibleFigureCanvas(self.figure)
        self.canvas.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.canvas.setMinimumSize(0, 0)
        layout.addWidget(self.canvas)
        self._asofs: List[date] = []
        self._asof_s: List[str] = []
        self._highlight_idx: int = 0
        self._cid = self.canvas.mpl_connect("button_press_event", self._on_click)
        self._sid = self.canvas.mpl_connect("scroll_event", self._on_scroll)
        self.canvas.setFocusPolicy(Qt.StrongFocus)

    def _finish_draw(self):
        self.canvas.setMinimumSize(0, 0)
        self.canvas.sync_figure_size()
        try:
            self.figure.tight_layout()
        except Exception:
            pass
        self.canvas.draw_idle()

    def clear(self, msg: str = "待加载"):
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.text(0.5, 0.5, msg, ha="center", va="center", fontsize=14, transform=ax.transAxes)
        ax.set_axis_off()
        self._finish_draw()
        self._asofs = []
        self._asof_s = []
        self._highlight_idx = 0

    def step_highlight(self, step: int) -> Optional[str]:
        if not self._asof_s:
            return None
        self._highlight_idx = int(
            np.clip(self._highlight_idx + step, 0, len(self._asof_s) - 1)
        )
        self.point_clicked.emit(self._asof_s[self._highlight_idx])
        return self._asof_s[self._highlight_idx]

    def _on_scroll(self, event):
        if not self._asof_s:
            return
        step = -1 if getattr(event, "step", 0) > 0 else 1
        self.step_highlight(step)

    def _asof_at_event(self, event) -> Optional[str]:
        if not self._asof_s:
            return None
        if event.inaxes is not None and event.xdata is not None:
            idx = int(round(event.xdata))
            idx = int(np.clip(idx, 0, len(self._asofs) - 1))
            return self._asof_s[idx]
        if 0 <= self._highlight_idx < len(self._asof_s):
            return self._asof_s[self._highlight_idx]
        return None

    def _on_click(self, event):
        """左键定位日期；右键弹出导出菜单。"""
        if not self._asof_s:
            return
        if getattr(event, "button", None) == 3:
            asof_s = self._asof_at_event(event)
            if asof_s:
                self._highlight_idx = self._asof_s.index(asof_s)
                self.point_clicked.emit(asof_s)
                self._popup_export_menu(asof_s)
            return
        if event.inaxes is None or event.xdata is None:
            return
        asof_s = self._asof_at_event(event)
        if not asof_s:
            return
        self._highlight_idx = self._asof_s.index(asof_s)
        self.point_clicked.emit(asof_s)

    def _popup_export_menu(self, asof_s: str):
        menu = QMenu(self)
        act = menu.addAction(f"导出 {asof_s} Excel…")
        chosen = menu.exec_(QCursor.pos())
        if chosen == act:
            self.export_requested.emit(asof_s)

    def render(self, reports: Dict[str, dict], highlight: Optional[str] = None):
        asofs = _union_starts(reports)
        if not asofs:
            reasons = [
                f"{v}: {(reports.get(v) or {}).get('empty_reason') or '无数据'}"
                for v in zb.VARIANTS
            ]
            self.clear("\n".join(reasons))
            return

        asof_s = [str(x) for x in asofs]
        self._asofs = asofs
        self._asof_s = asof_s
        x = np.arange(len(asofs))
        labels = [d.strftime("%m-%d") for d in asofs]

        if highlight and highlight in asof_s:
            self._highlight_idx = asof_s.index(highlight)
        else:
            self._highlight_idx = len(asofs) - 1

        # 预取各变体系列
        series: Dict[str, Dict[str, Dict[date, float]]] = {}
        for v in zb.VARIANTS:
            rep = reports.get(v) or {}
            day = _df_from_dict(rep.get("day_view"))
            roll = _df_from_dict(rep.get("roll_view"))
            cum = _df_from_dict(rep.get("cum_view"))
            series[v] = {
                "day_mean": _series_by_start(day, "start", "day_mean"),
                "n": _series_by_start(day, "start", "n"),
                "n_sel": _series_by_start(day, "start", "n_sel"),
                "win_rate": _series_by_start(day, "start", "win_rate"),
                "roll_mean": _series_by_start(roll, "asof", "roll_mean"),
                "roll_win": _series_by_start(roll, "asof", "roll_win"),
                "cum_mean": _series_by_start(cum, "start", "cum_mean"),
            }

        def ys(v: str, key: str) -> List[float]:
            m = series[v][key]
            return [m.get(a, np.nan) for a in asofs]

        legacy = bool(reports.get("_legacy_buy_point")) or bool(
            (reports.get(zb.VARIANT_A) or {}).get("legacy_buy_point")
        )

        self.figure.clear()
        ax1 = self.figure.add_subplot(311)
        ax2 = self.figure.add_subplot(312, sharex=ax1)
        ax3 = self.figure.add_subplot(313, sharex=ax1)

        # --- 1 近窗滚动 ---
        ax1.plot(x, ys(zb.VARIANT_A, "roll_mean"), color=C_TB, lw=1.3, marker="o", ms=3, label="全池·近窗日均%" if legacy else "买点A·近窗日均%")
        if not legacy:
            ax1.plot(x, ys(zb.VARIANT_B, "roll_mean"), color=C_NO, lw=1.3, marker="s", ms=3, label="买点B·近窗日均%")
        ax1.axhline(0, color="#999", lw=0.8, ls="--")
        ax1b = ax1.twinx()
        ax1b.plot(x, ys(zb.VARIANT_A, "roll_win"), color=C_TB_WIN, lw=1.2, ls="--", marker=".", ms=3, label="全池·日胜率%" if legacy else "买点A·日胜率%")
        if not legacy:
            ax1b.plot(x, ys(zb.VARIANT_B, "roll_win"), color=C_NO_WIN, lw=1.2, ls="--", marker=".", ms=3, label="买点B·日胜率%")
        ax1b.set_ylabel("日胜率%")
        ax1b.set_ylim(0, 100)
        lines1, labs1 = ax1.get_legend_handles_labels()
        lines2, labs2 = ax1b.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labs1 + labs2, loc="upper left", fontsize=8, ncol=2)
        ax1.set_ylabel("近窗日均%")
        ax1.set_title(
            "近窗滚动（开买日票均）· 全池"
            if legacy
            else "近窗滚动（开买日票均）· 买点A vs 买点B"
        )
        ax1.grid(True, alpha=0.25)

        # --- 2 开买日票均 + 成交笔数 ---
        ax2.plot(x, ys(zb.VARIANT_A, "day_mean"), color=C_TB, lw=1.3, marker="o", ms=3, zorder=3, label="全池·票均%" if legacy else "买点A·票均%")
        if not legacy:
            ax2.plot(x, ys(zb.VARIANT_B, "day_mean"), color=C_NO, lw=1.3, marker="s", ms=3, zorder=3, label="买点B·票均%")
        ax2.axhline(0, color="#999", lw=0.8, ls="--")
        ax2.set_ylabel("票均%")
        ax2b = ax2.twinx()
        n_meet = np.array(ys(zb.VARIANT_A, "n"), dtype=float)
        n_no = np.array(ys(zb.VARIANT_B, "n"), dtype=float)
        n_sel_meet = _n_sel_series_for_asofs(asofs, reports, zb.VARIANT_A)
        n_sel_no = _n_sel_series_for_asofs(asofs, reports, zb.VARIANT_B) if not legacy else [0.0] * len(asofs)
        bw = 0.18 if not legacy else 0.28
        if legacy:
            ax2b.bar(
                x,
                np.nan_to_num(n_sel_meet),
                width=bw,
                color="#7D3C98",
                alpha=0.55,
                label="选股只数",
                zorder=1,
            )
            ax2b.bar(
                x,
                np.nan_to_num(n_meet),
                width=bw * 0.65,
                color=C_TB,
                alpha=0.40,
                label="成交笔数",
                zorder=2,
            )
        else:
            ax2b.bar(
                x - 1.5 * bw,
                np.nan_to_num(n_sel_meet),
                width=bw,
                color="#7D3C98",
                alpha=0.55,
                label="买点A·选股只数",
                zorder=1,
            )
            ax2b.bar(
                x - 0.5 * bw,
                np.nan_to_num(n_meet),
                width=bw,
                color=C_TB,
                alpha=0.40,
                label="买点A·成交笔数",
                zorder=1,
            )
            ax2b.bar(
                x + 0.5 * bw,
                np.nan_to_num(n_sel_no),
                width=bw,
                color="#95A5A6",
                alpha=0.50,
                label="买点B·选股只数",
                zorder=1,
            )
            ax2b.bar(
                x + 1.5 * bw,
                np.nan_to_num(n_no),
                width=bw,
                color=C_NO,
                alpha=0.40,
                label="买点B·成交笔数",
                zorder=1,
            )
        ax2b.plot(
            x,
            ys(zb.VARIANT_A, "win_rate"),
            color=C_TB_WIN,
            lw=1.2,
            ls="--",
            marker=".",
            ms=3,
            label="全池·票胜率%" if legacy else "买点A·票胜率%",
            zorder=3,
        )
        if not legacy:
            ax2b.plot(
                x,
                ys(zb.VARIANT_B, "win_rate"),
                color=C_NO_WIN,
                lw=1.2,
                ls="--",
                marker=".",
                ms=3,
                label="买点B·票胜率%",
                zorder=3,
            )
            # 买点A选股占比
            tot = np.nan_to_num(n_sel_meet) + np.nan_to_num(n_sel_no)
            share = np.where(tot > 0, np.nan_to_num(n_sel_meet) / tot * 100.0, np.nan)
            ax2b.plot(
                x,
                share,
                color="#1ABC9C",
                lw=1.4,
                marker="D",
                ms=3,
                label="买点A·选股占比%",
                zorder=4,
            )
        try:
            cnt_hi = float(
                np.nanmax(np.concatenate([n_meet, n_no, n_sel_meet, n_sel_no]))
            ) if len(x) else 0.0
            if not legacy:
                cnt_hi = float(
                    np.nanmax(np.concatenate([n_meet, n_no, n_sel_meet, n_sel_no, share]))
                )
            y_hi = max(100.0, cnt_hi * 1.15 if cnt_hi > 0 else 100.0)
        except Exception:
            y_hi = 100.0
        ax2b.set_ylim(0, y_hi)
        ax2b.set_ylabel("票胜率% · 只数/笔数")
        lines1, labs1 = ax2.get_legend_handles_labels()
        lines2, labs2 = ax2b.get_legend_handles_labels()
        ax2.legend(lines1 + lines2, labs1 + labs2, loc="upper left", fontsize=7, ncol=2)
        ax2.set_title("开买日票均 · 选股只数/成交笔数 · 票胜率（叠画）")
        ax2.grid(True, alpha=0.25)

        # --- 3 累计 ---
        ax3.plot(x, ys(zb.VARIANT_A, "cum_mean"), color=C_TB, lw=1.3, marker="o", ms=3, label="全池·累计" if legacy else "买点A·累计")
        if not legacy:
            ax3.plot(x, ys(zb.VARIANT_B, "cum_mean"), color=C_NO, lw=1.3, marker="s", ms=3, label="买点B·累计")
        ax3.fill_between(x, ys(zb.VARIANT_A, "cum_mean"), 0, alpha=0.08, color=C_TB)
        if not legacy:
            ax3.fill_between(x, ys(zb.VARIANT_B, "cum_mean"), 0, alpha=0.08, color=C_NO)
        ax3.axhline(0, color="#999", lw=0.8, ls="--")
        ax3.set_ylabel("累计票均pp")
        ax3.set_title("累计开买日票均（简单加总，非资金曲线）")
        ax3.legend(loc="upper left", fontsize=8)
        ax3.grid(True, alpha=0.25)

        hi = self._highlight_idx
        for ax in (ax1, ax2, ax3):
            ax.axvline(hi, color="#E74C3C", lw=1.2, ls="-", alpha=0.8)

        ax3.set_xticks(x)
        ax3.set_xticklabels(labels, rotation=45, ha="right", fontsize=8)
        for ax in (ax1, ax2):
            plt.setp(ax.get_xticklabels(), visible=False)

        self._finish_draw()


class BbPctbPullbackMonitorDialog(QDialog):
    def __init__(
        self,
        parent=None,
        auto_run: bool = False,
        window: int = 10,
        months: int = 2,
    ):
        super().__init__(parent)
        self.setWindowTitle("蚂蚁量化 - 布林%b回落选股监控")
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowMinimizeButtonHint
        )
        self._auto_run = bool(auto_run)
        self._worker: Optional[MonitorWorker] = None
        self._prepare_worker: Optional[PrepareDataWorker] = None
        self._prepare_max_days = 15
        self._reports: Dict[str, dict] = {}

        root = QVBoxLayout(self)

        top = QHBoxLayout()
        root.addLayout(top)
        self.status_label = QLabel("待加载：点击“刷新”")
        self.status_label.setStyleSheet("color: #333;")
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        top.addWidget(self.status_label, 1)

        top.addWidget(QLabel("近窗天数:"))
        self.window_spin = QSpinBox()
        self.window_spin.setRange(3, 60)
        self.window_spin.setValue(int(window))
        top.addWidget(self.window_spin)

        top.addWidget(QLabel("月份:"))
        self.months_spin = QSpinBox()
        self.months_spin.setRange(1, 36)
        self.months_spin.setValue(int(months))
        top.addWidget(self.months_spin)

        top.addWidget(QLabel("定位开买日:"))
        self.asof_combo = QComboBox()
        self.asof_combo.setMinimumWidth(120)
        self.asof_combo.currentTextChanged.connect(self._on_asof_combo)
        top.addWidget(self.asof_combo)

        self.prepare_btn = QPushButton("更新选股+回测")
        self.prepare_btn.setToolTip(
            "按回测选股日补缺口，并重跑最近已有选股日；\n"
            f"选股=布林%b回落选股；买入=次日开盘；卖出=bb_pctb（斜率/%b/12日）；\n"
            f"持有最多12个交易日强清；上限 {self._prepare_max_days} 天。"
        )
        self.prepare_btn.clicked.connect(self.prepare_data)
        top.addWidget(self.prepare_btn)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self.refresh_btn)

        self.export_btn = QPushButton("导出当日Excel")
        self.export_btn.setToolTip(
            "导出当前开买日：前一日选股（买点A/B）、开买收益明细、近窗对比（也可在图上右键）"
        )
        self.export_btn.clicked.connect(self.export_current_asof)
        top.addWidget(self.export_btn)

        self.open_dir_btn = QPushButton("打开数据目录")
        self.open_dir_btn.clicked.connect(self._open_dir)
        top.addWidget(self.open_dir_btn)

        self.stock_type_btn = QPushButton("股票类型")
        self.stock_type_btn.setToolTip(
            "勾选参与统计的股票类型（ST/沪主板/深主板/创业板/科创板/北交所）\n"
            "四个监控共用同一配置文件"
        )
        self.stock_type_btn.clicked.connect(self._open_stock_type_cfg)
        top.addWidget(self.stock_type_btn)

        split = QSplitter(Qt.Vertical)
        root.addWidget(split, 1)
        self.chart = CompareChartWidget()
        self.chart.point_clicked.connect(self.show_asof)
        self.chart.export_requested.connect(self.export_asof)
        split.addWidget(self.chart)
        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(72)
        self.detail.setMaximumHeight(96)
        self.detail.setFont(QFont("Microsoft YaHei", 9))
        split.addWidget(self.detail)
        split.setStretchFactor(0, 8)
        split.setStretchFactor(1, 2)

        self.chart.clear()
        sc_left = QShortcut(QKeySequence(Qt.Key_Left), self)
        sc_left.activated.connect(lambda: self._step_asof(-1))
        sc_right = QShortcut(QKeySequence(Qt.Key_Right), self)
        sc_right.activated.connect(lambda: self._step_asof(1))
        if self._auto_run:
            QTimer.singleShot(250, self.refresh)
        self._work_area_fitted = False
        QTimer.singleShot(0, self._fit_work_area)

    def showEvent(self, event):
        super().showEvent(event)
        if not self._work_area_fitted:
            self._fit_work_area()

    def _fit_work_area(self):
        """首次显示时居中到工作区；之后保留用户拖动的位置与尺寸。"""
        if self._work_area_fitted:
            return
        self._work_area_fitted = True
        self.setMinimumSize(720, 480)
        self.setMaximumWidth(16777215)
        app = QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
        if screen is None:
            self.resize(MONITOR_WIDTH, MONITOR_HEIGHT)
            return
        geo = screen.availableGeometry()
        width = min(MONITOR_WIDTH, geo.width())
        height = min(MONITOR_HEIGHT, geo.height())
        x = geo.x() + max(0, (geo.width() - width) // 2)
        y = geo.y() + max(0, (geo.height() - height) // 2)
        self.setGeometry(x, y, width, height)

    def _step_asof(self, step: int):
        if not self._reports:
            return
        asof_s = self.chart.step_highlight(step)
        if asof_s:
            self.show_asof(asof_s)

    def _open_dir(self):
        path = str(zb.DIR)
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            QMessageBox.information(self, "数据目录", path)

    def _open_stock_type_cfg(self):
        try:
            from utils.monitor_stock_type_filter import open_stock_type_dialog
        except Exception as e:
            QMessageBox.warning(self, "股票类型", f"无法打开配置: {e}")
            return
        if open_stock_type_dialog(self):
            self.refresh()

    def export_current_asof(self):
        asof_s = (self.asof_combo.currentText() or "").strip()
        if not asof_s:
            QMessageBox.information(self, "导出", "请先刷新并定位一个开买日。")
            return
        self.export_asof(asof_s)

    def export_asof(self, asof_s: str):
        if not self._reports:
            QMessageBox.information(self, "导出", "请先点击「刷新」加载数据。")
            return
        asof_s = (asof_s or "").strip()
        if not asof_s:
            return
        try:
            asof_d = date.fromisoformat(asof_s)
        except Exception:
            QMessageBox.warning(self, "导出", f"日期无效: {asof_s}")
            return

        default_name = f"布林%b回落对比_{asof_s}.xlsx"
        default_path = str(zb.DIR / default_name)
        path, _ = QFileDialog.getSaveFileName(
            self,
            f"导出 {asof_s} Excel",
            default_path,
            "Excel (*.xlsx)",
        )
        if not path:
            return
        if not path.lower().endswith(".xlsx"):
            path += ".xlsx"

        window = int(self.window_spin.value())
        for v in zb.VARIANTS:
            w = (self._reports.get(v) or {}).get("window")
            if w:
                window = int(w)
                break

        self.status_label.setText(f"正在导出 {asof_s} …")
        QApplication.processEvents()
        try:
            out = zb.export_asof_report(
                asof_d,
                Path(path),
                reports=self._reports,
                window=window,
            )
        except Exception as e:
            self.status_label.setText(f"导出失败: {e}")
            QMessageBox.critical(self, "导出失败", str(e))
            return
        self.status_label.setText(f"已导出: {out.name}")
        QMessageBox.information(self, "导出完成", f"已保存:\n{out}")

    def prepare_data(self):
        if self._prepare_worker and self._prepare_worker.isRunning():
            return
        if self._worker and self._worker.isRunning():
            QMessageBox.information(self, "请稍候", "当前正在刷新图表，请等刷新结束后再准备数据。")
            return
        self.prepare_btn.setEnabled(False)
        self.refresh_btn.setEnabled(False)
        self.status_label.setText(
            f"准备数据中（缺口选股 + 双回测，上限 {self._prepare_max_days} 天）…"
        )
        self._prepare_worker = PrepareDataWorker(max_days=self._prepare_max_days, parent=self)
        self._prepare_worker.progress.connect(self._on_prepare_progress)
        self._prepare_worker.finished_ok.connect(self._on_prepare_ok)
        self._prepare_worker.failed.connect(self._on_prepare_fail)
        self._prepare_worker.start()

    def _on_prepare_progress(self, msg: str):
        tip = (msg or "").strip()
        if len(tip) > 140:
            tip = tip[:137] + "…"
        self.status_label.setText(tip or "准备数据中…")

    def _on_prepare_ok(self, msg: str):
        self.prepare_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self.status_label.setText(msg)
        QMessageBox.information(
            self,
            "准备完成",
            f"{msg}\n\n请点击「刷新」加载最新按票数据。",
        )

    def _on_prepare_fail(self, err: str):
        self.prepare_btn.setEnabled(True)
        self.refresh_btn.setEnabled(True)
        self.status_label.setText("准备失败")
        QMessageBox.warning(self, "准备失败", err)

    def refresh(self):
        if self._worker and self._worker.isRunning():
            return
        if self._prepare_worker and self._prepare_worker.isRunning():
            QMessageBox.information(self, "请稍候", "正在更新选股+回测，请等完成后再刷新。")
            return
        self.refresh_btn.setEnabled(False)
        self.prepare_btn.setEnabled(False)
        self.status_label.setText("计算中…")
        self.chart.clear("计算中，请稍候…")
        self._worker = MonitorWorker(
            window=int(self.window_spin.value()),
            months=int(self.months_spin.value()),
            parent=self,
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished_ok.connect(self._on_ok)
        self._worker.failed.connect(self._on_fail)
        self._worker.start()

    def _on_progress(self, msg: str):
        self.status_label.setText(msg)

    def _on_fail(self, err: str):
        self.refresh_btn.setEnabled(True)
        self.prepare_btn.setEnabled(True)
        self.status_label.setText("失败")
        self.chart.clear("加载失败")
        QMessageBox.warning(self, "加载失败", err)

    def _on_ok(self, payload: dict):
        self.refresh_btn.setEnabled(True)
        self.prepare_btn.setEnabled(True)
        reports = payload.get("reports") or {}
        self._reports = reports
        bits = []
        for v in zb.VARIANTS:
            rep = reports.get(v) or {}
            n = int(rep.get("n_start_days") or 0)
            if n:
                full = rep.get("full") or {}
                bits.append(f"{v}:票均{_fmt_pct(full.get('mean'))}/胜率{full.get('win')}%")
            else:
                bits.append(f"{v}:暂无数据")
        # 量柱核对：选股日均只数（买点A vs B）
        try:
            sc_m = _sel_count_map(reports, zb.VARIANT_A)
            sc_n = _sel_count_map(reports, zb.VARIANT_B)
            if sc_m and sc_n:
                avg_m = sum(sc_m.values()) / max(1, len(sc_m))
                avg_n = sum(sc_n.values()) / max(1, len(sc_n))
                share = avg_m / max(1e-9, avg_m + avg_n) * 100.0
                bits.append(
                    f"选股日均:买点A{avg_m:.0f}/买点B{avg_n:.0f}(A占比{share:.1f}%)"
                )
        except Exception:
            pass
        try:
            from utils.monitor_stock_type_filter import summarize_config

            type_tip = summarize_config()
        except Exception:
            type_tip = ""
        self.status_label.setText(
            " | ".join(bits)
            + ("  · 蓝=买点A / 橙=买点B" if not self._reports.get("_legacy_buy_point") else "  · 旧版选股无「买点」列，两组暂同全池；请「更新选股+回测」重选")
            + (f"  · {type_tip}" if type_tip else "")
        )

        starts = [str(x) for x in _union_starts(reports)]
        self.asof_combo.blockSignals(True)
        self.asof_combo.clear()
        self.asof_combo.addItems(starts)
        hi = starts[-1] if starts else None
        if hi:
            self.asof_combo.setCurrentText(hi)
        self.asof_combo.blockSignals(False)
        self.chart.render(reports, highlight=hi)
        if hi:
            self.show_asof(hi)

    def _on_asof_combo(self, text: str):
        if text and self._reports:
            self.chart.render(self._reports, highlight=text)
            self.show_asof(text)

    def _day_row(self, rep: dict, asof_s: str) -> Optional[pd.Series]:
        day = _df_from_dict(rep.get("day_view"))
        if day.empty or "start" not in day.columns:
            return None
        day = day.copy()
        day["start"] = pd.to_datetime(day["start"], errors="coerce").dt.date.astype(str)
        row = day[day["start"] == asof_s]
        return row.iloc[0] if not row.empty else None

    def _roll_row(self, rep: dict, asof_s: str) -> Optional[pd.Series]:
        roll = _df_from_dict(rep.get("roll_view"))
        if roll.empty or "asof" not in roll.columns:
            return None
        roll = roll.copy()
        roll["asof"] = pd.to_datetime(roll["asof"], errors="coerce").dt.date.astype(str)
        rr = roll[roll["asof"] == asof_s]
        return rr.iloc[0] if not rr.empty else None

    def show_asof(self, asof_s: str):
        if not self._reports or not asof_s:
            return
        self.asof_combo.blockSignals(True)
        if self.asof_combo.findText(asof_s) >= 0:
            self.asof_combo.setCurrentText(asof_s)
        self.asof_combo.blockSignals(False)
        self.chart.render(self._reports, highlight=asof_s)

        lines: List[str] = [f"【开买日 {asof_s} · 对比】"]
        try:
            asof_d = date.fromisoformat(asof_s)
            prev = zb.prev_trading_day(asof_d)
            meet_map = _sel_count_map(self._reports, zb.VARIANT_A)
            not_map = _sel_count_map(self._reports, zb.VARIANT_B)
            n_meet = meet_map.get(prev) if prev is not None else None
            n_not = not_map.get(prev) if prev is not None else None
            lines.append(
                f"前一日选股 {prev or '—'}: 买点A {n_meet if n_meet is not None else '—'} 只 / "
                f"买点B {n_not if n_not is not None else '—'} 只"
            )
        except Exception:
            pass
        win = None
        for v in zb.VARIANTS:
            rep = self._reports.get(v) or {}
            if win is None:
                win = rep.get("window")
            r = self._day_row(rep, asof_s)
            rr = self._roll_row(rep, asof_s)
            if r is not None:
                lines.append(
                    f"{v}: 票均 {_fmt_pct(r.get('day_mean'))}  "
                    f"成交 {int(r.get('n') or 0)}  "
                    f"票胜率 {_fmt_pct(r.get('win_rate'))}"
                )
            else:
                lines.append(f"{v}: （该开买日无已了结样本）")
            if rr is not None:
                lines.append(
                    f"  近窗{win}: 日均 {_fmt_pct(rr.get('roll_mean'))}  "
                    f"日胜率 {_fmt_pct(rr.get('roll_win'))}  "
                    f"总笔数 {int(rr.get('roll_n') or 0)}  "
                    f"（{rr.get('window_from')} → {rr.get('window_to')}）"
                )

        for v in zb.VARIANTS:
            rep = self._reports.get(v) or {}
            lines.append("")
            lines.append(f"—— {v} 票明细 ——")
            lines.append("代码   名称       选股日       收益%     清仓日      %b        备注")
            lines.append("-" * 78)
            trades = (rep.get("trades") or {}).get(asof_s) or []
            if not trades:
                lines.append("（无）")
            else:
                for t in trades:
                    pb = t.get("pctb")
                    pb_s = "" if pb is None else f"{float(pb):.4f}"
                    lines.append(
                        f"{t.get('code',''):<6} {(t.get('name') or '')[:8]:<8} "
                        f"{t.get('sel',''):<12} {_fmt_pct(t.get('ret')):>8}  "
                        f"{t.get('known_on',''):<10} {pb_s:>6}  {t.get('note','')}"
                    )

        lines.append("")
        for v in zb.VARIANTS:
            rep = self._reports.get(v) or {}
            full = rep.get("full") or {}
            lines.append(
                f"{v} 全样本 asof={rep.get('last_close')}: "
                f"票均{_fmt_pct(full.get('mean'))} 胜率{full.get('win')}% "
                f"笔数{full.get('n_known')} 未完成{full.get('n_open')} | "
                f"{rep.get('trade_dir') or ''}"
            )
        lines.append("口径：横轴=开买日；蓝=买点A、橙=买点B；累计=开买日票均简单加总；分组=选股表「买点」列。")
        self.detail.setPlainText("\n".join(lines))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="布林%b回落选股 · 买点A/B对比监控")
    ap.add_argument("--auto-run", action="store_true")
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--months", type=int, default=2)
    args = ap.parse_args(argv)

    app = QApplication.instance() or QApplication(sys.argv)
    dlg = BbPctbPullbackMonitorDialog(
        auto_run=bool(args.auto_run),
        window=int(args.window),
        months=int(args.months),
    )
    dlg.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
