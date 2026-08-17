#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
马总 MA10 · 风格切换监控 GUI（独立版）

- 近 N 个月滚动决策：CORE / +七月风 / +六月风
- 近窗得分曲线：CORE、七月、六月、仅B、非B
- 日线回测收益：上MA10 按票收益率按选股日票均，以及「上周决策→本周执行包」实现
- 点击图上某日 → 看 asof 详情与失效读法

用法:
  python ma10_regime_monitor_gui.py
  python ma10_regime_monitor_gui.py --auto-run
  python ma10_regime_monitor_gui.py --window 15 --months 3
"""
from __future__ import annotations

import argparse
import os
import sys
import warnings
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd
from PyQt5.QtCore import QThread, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
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

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "tools"))

import ma10_regime_switch as rs  # noqa: E402

plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

PACK_COL = {
    "CORE_ONLY": "core_ok",
    "CORE_PLUS_JULY": "pack_july",
    "CORE_PLUS_JUNE": "pack_june",
}
PACK_COLOR = {
    "CORE_ONLY": "#2E86AB",
    "CORE_PLUS_JULY": "#E67E22",
    "CORE_PLUS_JUNE": "#27AE60",
}
DEC_LABEL = {
    "CORE_ONLY": "CORE",
    "CORE_PLUS_JULY": "+七月",
    "CORE_PLUS_JUNE": "+六月",
}


def iso_year_week(d: date) -> tuple:
    ic = d.isocalendar()
    return int(ic[0]), int(ic[1])


def weekly_exec_decision_map(hist: List[dict]) -> Dict[date, Optional[str]]:
    """实盘周频：上周最后选股日的决策 → 本周每个选股日共用。

    色带仍可按日展示「当天会建议什么」；执行包收益必须按周持有，避免日频切换高估。
    """
    if not hist:
        return {}
    # 每个 ISO 周 → 该周最后一个 asof 的决策
    week_last: Dict[tuple, str] = {}
    week_order: List[tuple] = []
    for h in hist:
        wk = iso_year_week(h["asof"])
        if wk not in week_last:
            week_order.append(wk)
        week_last[wk] = h["decision"]

    prev_week_dec: Dict[tuple, str] = {}
    for i, wk in enumerate(week_order):
        if i == 0:
            continue
        prev_week_dec[wk] = week_last[week_order[i - 1]]

    out: Dict[date, Optional[str]] = {}
    for h in hist:
        out[h["asof"]] = prev_week_dec.get(iso_year_week(h["asof"]))
    return out


def _fmt_pct(v: Any) -> str:
    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
        return "NA"
    try:
        return f"{float(v):+.2f}%"
    except Exception:
        return "NA"


def daily_sleeve_means(df: pd.DataFrame) -> pd.DataFrame:
    """选股日 → 各袖套票均收益率%（日线回测按票结果）。"""
    cols = [
        "baseline",
        "core_ok",
        "july_sat",
        "june_sat",
        "pack_july",
        "pack_june",
        "b_only",
        "non_b",
    ]
    rows = []
    for sel, g in df.groupby("sel"):
        row: Dict[str, Any] = {"sel": sel}
        row["baseline"] = float(g["ret"].mean()) if len(g) else None
        for c in cols[1:]:
            sub = g[g[c].fillna(False)]
            row[c] = float(sub["ret"].mean()) if len(sub) else None
            row[f"{c}_n"] = int(len(sub))
        rows.append(row)
    out = pd.DataFrame(rows).sort_values("sel").reset_index(drop=True)
    return out


def load_day_summary_overlay(dir_path: Path) -> pd.DataFrame:
    """合并各日选股收益汇总（非按票）；总收益率% 为组合净值口径，可能被现金摊薄。"""
    files = sorted(
        p
        for p in dir_path.glob("各日选股收益汇总_日线-ma10-单点_*.xlsx")
        if "按票" not in p.name
    )
    if not files:
        return pd.DataFrame(columns=["sel", "basket_ret"])
    parts = []
    for p in files:
        try:
            d = pd.read_excel(p)
        except Exception:
            continue
        if "选股日" not in d.columns or "总收益率%" not in d.columns:
            continue
        t = pd.DataFrame(
            {
                "sel": pd.to_datetime(d["选股日"], errors="coerce").dt.date,
                "basket_ret": pd.to_numeric(d["总收益率%"], errors="coerce"),
                "src": p.name,
            }
        )
        parts.append(t.dropna(subset=["sel"]))
    if not parts:
        return pd.DataFrame(columns=["sel", "basket_ret"])
    all_df = pd.concat(parts, ignore_index=True)
    all_df = all_df.sort_values("sel").drop_duplicates("sel", keep="last")
    return all_df.reset_index(drop=True)


class MonitorWorker(QThread):
    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, window: int, months: int, edge: float, parent=None):
        super().__init__(parent)
        self.window = int(window)
        self.months = int(months)
        self.edge = float(edge)

    def run(self):
        try:
            self.progress.emit("加载收盘上MA10按票回测…")
            files = rs.discover_files()
            if not files:
                raise FileNotFoundError(f"未找到按票文件: {rs.DIR}")
            df = rs.add_flags(rs.load_pool(files))
            sels_all = sorted(df["sel"].unique())
            if len(sels_all) < self.window:
                raise RuntimeError(f"选股日不足 window={self.window}，仅 {len(sels_all)} 天")

            end = sels_all[-1]
            # 约 months 个自然月的选股日窗口（含滚动所需前置天数）
            cut = end - timedelta(days=int(self.months * 31))
            sels_view = [d for d in sels_all if d >= cut]
            # 滚动需要 window-1 天预热，从全历史取
            self.progress.emit("计算滚动风格决策…")
            hist: List[dict] = []
            for i in range(self.window - 1, len(sels_all)):
                asof = sels_all[i]
                if asof < cut:
                    continue
                win = sels_all[i - self.window + 1 : i + 1]
                sc = rs.score_window(df, win)
                dec = rs.decide(sc, edge=self.edge)
                hist.append(
                    {
                        "asof": asof,
                        "asof_s": str(asof),
                        "decision": dec["decision"],
                        "reason": dec["reason"],
                        "rules": dec["rules"],
                        "rule_ui": rs.RULE_UI.get(dec["decision"], ""),
                        "decay_hint": rs.decay_hint(sc, edge=self.edge),
                        "scores": sc,
                        "july_day": sc["july_sat"]["day_mean"],
                        "june_day": sc["june_sat"]["day_mean"],
                        "core_day": sc["core_ok"]["day_mean"],
                        "pack_july_day": sc["pack_july"]["day_mean"],
                        "pack_june_day": sc["pack_june"]["day_mean"],
                        "b_only_day": sc["b_only"]["day_mean"],
                        "non_b_day": sc["non_b"]["day_mean"],
                        "window_from": str(win[0]),
                        "window_to": str(win[-1]),
                    }
                )

            self.progress.emit("汇总日线回测票均收益…")
            daily = daily_sleeve_means(df)
            daily = daily[daily["sel"].isin(sels_view)].reset_index(drop=True)
            basket = load_day_summary_overlay(rs.DIR)

            # 执行包：上周末决策 → 本周每日票均（周频持有，与实盘一致；避免日频 T-1 高估切换）
            dec_map = {h["asof"]: h["decision"] for h in hist}
            exec_by_day = weekly_exec_decision_map(hist)
            exec_rets = []
            exec_decisions = []
            for _, row in daily.iterrows():
                sel = row["sel"]
                decision = exec_by_day.get(sel)
                exec_decisions.append(decision)
                if not decision:
                    exec_rets.append(None)
                    continue
                col = PACK_COL.get(decision)
                exec_rets.append(row[col] if col in row.index else None)
            daily = daily.copy()
            daily["exec_pack"] = exec_rets
            daily["exec_decision"] = exec_decisions  # 上周决策（本周执行）
            daily["decision"] = [dec_map.get(s) for s in daily["sel"]]  # 当日建议（色带）

            payload = {
                "files": [p.name for p in files],
                "data_from": str(sels_all[0]),
                "data_to": str(sels_all[-1]),
                "n_sel_days": len(sels_all),
                "view_from": str(sels_view[0]) if sels_view else "",
                "view_to": str(sels_view[-1]) if sels_view else "",
                "window": self.window,
                "months": self.months,
                "edge": self.edge,
                "history": hist,
                "daily": daily,
                "basket": basket,
                "latest": hist[-1] if hist else None,
            }
            self.finished_ok.emit(payload)
        except Exception as e:
            self.failed.emit(str(e))


class RegimeChartWidget(QWidget):
    point_clicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.figure = Figure(figsize=(11, 7), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas)
        self._asofs: List[date] = []
        self._cid = self.canvas.mpl_connect("button_press_event", self._on_click)

    def _on_click(self, event):
        if event.inaxes is None or event.xdata is None or not self._asofs:
            return
        idx = int(round(event.xdata))
        idx = max(0, min(idx, len(self._asofs) - 1))
        self.point_clicked.emit(str(self._asofs[idx]))

    def clear(self, msg: str = "点击“刷新”加载近三个月数据"):
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.text(0.5, 0.5, msg, ha="center", va="center", transform=ax.transAxes, fontsize=12)
        ax.set_axis_off()
        self.canvas.draw_idle()
        self._asofs = []

    def update_chart(self, payload: dict, highlight: Optional[str] = None):
        hist = payload.get("history") or []
        daily: pd.DataFrame = payload.get("daily")
        if not hist:
            self.clear("无滚动决策数据（选股日不足或窗口过大）")
            return

        asofs = [h["asof"] for h in hist]
        self._asofs = asofs
        x = np.arange(len(asofs))
        labels = [f"{d.month}/{d.day}" for d in asofs]

        self.figure.clear()
        gs = self.figure.add_gridspec(4, 1, height_ratios=[0.75, 0.75, 1.35, 1.2], hspace=0.12)
        ax_exec = self.figure.add_subplot(gs[0])
        ax_sug = self.figure.add_subplot(gs[1], sharex=ax_exec)
        ax1 = self.figure.add_subplot(gs[2], sharex=ax_exec)
        ax2 = self.figure.add_subplot(gs[3], sharex=ax_exec)

        ymap = {"CORE_ONLY": 0, "CORE_PLUS_JULY": 1, "CORE_PLUS_JUNE": 2}

        # --- 本周执行包（周频，看这个才是实盘在跑哪包）---
        exec_decisions: List[Optional[str]] = []
        if daily is not None and not daily.empty:
            dmap0 = {row["sel"]: row for _, row in daily.iterrows()}
            for a in asofs:
                r = dmap0.get(a)
                exec_decisions.append(None if r is None else r.get("exec_decision"))
        else:
            exec_decisions = [None] * len(asofs)

        for i, ed in enumerate(exec_decisions):
            if not ed:
                ax_exec.axvspan(i - 0.5, i + 0.5, color="#BDC3C7", alpha=0.35, lw=0)
            else:
                ax_exec.axvspan(i - 0.5, i + 0.5, color=PACK_COLOR.get(ed, "#888"), alpha=0.45, lw=0)
        ys_exec = [ymap[ed] if ed in ymap else float("nan") for ed in exec_decisions]
        ax_exec.plot(x, ys_exec, "k.-", lw=1.2, ms=5)
        ax_exec.set_yticks([0, 1, 2])
        ax_exec.set_yticklabels(["CORE", "+七月", "+六月"])
        ax_exec.set_ylim(-0.4, 2.4)
        ax_exec.set_ylabel("本周执行")
        ax_exec.set_title(
            f"MA10 风格监控  |  window={payload['window']}  "
            f"视图 {payload['view_from']} → {payload['view_to']}  "
            f"(数据 {payload['data_from']} → {payload['data_to']})  |  "
            f"上条=本周执行(上周定)  下条=当日建议",
            fontsize=10,
        )
        ax_exec.grid(True, axis="x", alpha=0.25)
        # 周界虚线 + 周标签
        last_wk = None
        for i, a in enumerate(asofs):
            wk = iso_year_week(a)
            if wk != last_wk:
                if i > 0:
                    ax_exec.axvline(i - 0.5, color="#333", lw=0.8, ls=":", alpha=0.6)
                    ax_sug.axvline(i - 0.5, color="#333", lw=0.8, ls=":", alpha=0.5)
                ed0 = exec_decisions[i]
                lab = DEC_LABEL.get(str(ed0), "—") if ed0 else "—"
                ax_exec.text(
                    i,
                    2.15,
                    f"W{wk[1]}:{lab}",
                    fontsize=7,
                    color="#222",
                    ha="left",
                    va="top",
                )
                last_wk = wk

        # --- 当日建议（可日变，仅供参考）---
        for i, h in enumerate(hist):
            c = PACK_COLOR.get(h["decision"], "#888")
            ax_sug.axvspan(i - 0.5, i + 0.5, color=c, alpha=0.3, lw=0)
        ys = [ymap.get(h["decision"], 0) for h in hist]
        ax_sug.plot(x, ys, "k.-", lw=1.0, ms=4)
        ax_sug.set_yticks([0, 1, 2])
        ax_sug.set_yticklabels(["CORE", "+七月", "+六月"])
        ax_sug.set_ylim(-0.4, 2.4)
        ax_sug.set_ylabel("当日建议")
        ax_sug.grid(True, axis="x", alpha=0.25)

        # --- 近窗得分（与 decide 同一口径：CORE / 七月整包 / 六月整包）---
        series = [
            ("core_day", "CORE近窗", "#2E86AB"),
            ("pack_july_day", "七月整包近窗", "#E67E22"),
            ("pack_june_day", "六月整包近窗", "#27AE60"),
            ("b_only_day", "仅B近窗", "#8E44AD"),
            ("non_b_day", "非B近窗", "#C0392B"),
        ]
        for key, lab, color in series:
            vals = [h.get(key) for h in hist]
            ax1.plot(x, vals, "-o", ms=3, lw=1.2, label=lab, color=color)
        ax1.axhline(0, color="#666", lw=0.8, ls="--")
        ax1.legend(loc="upper left", ncol=5, fontsize=8, framealpha=0.9)
        ax1.set_ylabel("近窗日均%")
        ax1.grid(True, alpha=0.3)

        # --- 日线回测当日票均（与近窗同一套袖套分类）---
        if daily is not None and not daily.empty:
            dmap = {row["sel"]: row for _, row in daily.iterrows()}

            def _series(col: str):
                return [dmap[a][col] if a in dmap else None for a in asofs]

            ax2.plot(x, _series("baseline"), "-", ms=2, lw=0.9, color="#7F8C8D", alpha=0.7, label="上MA10")
            ax2.plot(x, _series("core_ok"), "-o", ms=2.5, lw=1.1, color="#2E86AB", label="CORE")
            ax2.plot(x, _series("pack_july"), "-o", ms=2.5, lw=1.1, color="#E67E22", label="七月整包")
            ax2.plot(x, _series("pack_june"), "-o", ms=2.5, lw=1.1, color="#27AE60", label="六月整包")
            ax2.plot(x, _series("b_only"), "--", lw=1.1, color="#8E44AD", label="仅B")
            ax2.plot(x, _series("non_b"), "--", lw=1.1, color="#C0392B", label="非B")
            ax2.plot(
                x,
                _series("exec_pack"),
                "-o",
                ms=3.5,
                lw=1.6,
                color="#1A5276",
                label="执行包(上周→本周)",
            )
        basket = payload.get("basket")
        if basket is not None and not basket.empty:
            bmap = dict(zip(basket["sel"], basket["basket_ret"]))
            bvals = [bmap.get(a) for a in asofs]
            if any(v is not None and not (isinstance(v, float) and np.isnan(v)) for v in bvals):
                ax2.plot(
                    x,
                    bvals,
                    ":",
                    lw=1.0,
                    color="#16A085",
                    alpha=0.75,
                    label="组合总收益%(汇总)",
                )
        ax2.axhline(0, color="#666", lw=0.8, ls="--")
        ax2.legend(loc="upper left", ncol=4, fontsize=8, framealpha=0.9)
        ax2.set_ylabel("回测当日%")
        ax2.grid(True, alpha=0.3)

        # x labels
        step = max(1, len(labels) // 12)
        ax2.set_xticks(x[::step])
        ax2.set_xticklabels([labels[i] for i in range(0, len(labels), step)], rotation=0)
        for ax in (ax_exec, ax_sug, ax1):
            plt.setp(ax.get_xticklabels(), visible=False)

        if highlight:
            try:
                hi = date.fromisoformat(highlight)
                if hi in asofs:
                    ix = asofs.index(hi)
                    for ax in (ax_exec, ax_sug, ax1, ax2):
                        ax.axvline(ix, color="#E74C3C", lw=1.2, alpha=0.8)
            except Exception:
                pass

        self.figure.tight_layout()
        self.canvas.draw_idle()


class Ma10RegimeMonitorDialog(QDialog):
    def __init__(
        self,
        parent=None,
        auto_run: bool = False,
        window: int = 15,
        months: int = 3,
        edge: float = rs.LEAD_EDGE,
    ):
        super().__init__(parent)
        self.setWindowTitle("蚂蚁量化 - MA10风格切换监控")
        self.resize(1280, 860)
        self._auto_run = bool(auto_run)
        self._worker: Optional[MonitorWorker] = None
        self._payload: Optional[dict] = None
        self._hist_by_asof: Dict[str, dict] = {}

        root = QVBoxLayout(self)

        top = QHBoxLayout()
        root.addLayout(top)
        self.status_label = QLabel("待加载：点击“刷新”")
        self.status_label.setStyleSheet("color: #333;")
        top.addWidget(self.status_label, 1)

        top.addWidget(QLabel("近窗天数:"))
        self.window_spin = QSpinBox()
        self.window_spin.setRange(5, 60)
        self.window_spin.setValue(int(window))
        top.addWidget(self.window_spin)

        top.addWidget(QLabel("月份:"))
        self.months_spin = QSpinBox()
        self.months_spin.setRange(1, 12)
        self.months_spin.setValue(int(months))
        top.addWidget(self.months_spin)

        top.addWidget(QLabel("edge:"))
        self.edge_spin = QDoubleSpinBox()
        self.edge_spin.setRange(0.0, 5.0)
        self.edge_spin.setSingleStep(0.05)
        self.edge_spin.setDecimals(2)
        self.edge_spin.setValue(float(edge))
        top.addWidget(self.edge_spin)

        top.addWidget(QLabel("定位asof:"))
        self.asof_combo = QComboBox()
        self.asof_combo.setMinimumWidth(120)
        self.asof_combo.currentTextChanged.connect(self._on_asof_combo)
        top.addWidget(self.asof_combo)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self.refresh_btn)

        self.open_dir_btn = QPushButton("打开数据目录")
        self.open_dir_btn.clicked.connect(self._open_dir)
        top.addWidget(self.open_dir_btn)

        split = QSplitter(Qt.Vertical)
        root.addWidget(split, 1)

        self.chart = RegimeChartWidget()
        self.chart.point_clicked.connect(self.show_asof)
        split.addWidget(self.chart)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(180)
        _f = QFont("Consolas")
        if _f.pointSize() <= 0:
            _f.setPointSize(10)
        else:
            _f.setPointSize(max(9, _f.pointSize()))
        # 中文环境用微软雅黑更稳
        self.detail.setFont(QFont("Microsoft YaHei", 10))
        split.addWidget(self.detail)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)

        self.chart.clear()
        if self._auto_run:
            QTimer.singleShot(250, self.refresh)

    def _open_dir(self):
        path = str(rs.DIR)
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception:
            QMessageBox.information(self, "数据目录", path)

    def refresh(self):
        if self._worker and self._worker.isRunning():
            return
        self.refresh_btn.setEnabled(False)
        self.status_label.setText("计算中…")
        self.chart.clear("计算中，请稍候…")
        self._worker = MonitorWorker(
            window=int(self.window_spin.value()),
            months=int(self.months_spin.value()),
            edge=float(self.edge_spin.value()),
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
        self.status_label.setText("失败")
        self.chart.clear("加载失败")
        QMessageBox.warning(self, "加载失败", err)

    def _on_ok(self, payload: dict):
        self.refresh_btn.setEnabled(True)
        self._payload = payload
        hist = payload.get("history") or []
        self._hist_by_asof = {h["asof_s"]: h for h in hist}
        self.asof_combo.blockSignals(True)
        self.asof_combo.clear()
        self.asof_combo.addItems([h["asof_s"] for h in hist])
        self.asof_combo.blockSignals(False)

        n_files = len(payload.get("files") or [])
        latest = payload.get("latest") or {}
        dec = latest.get("decision", "")
        self.status_label.setText(
            f"已加载 {n_files} 个按票文件 | 选股日 {payload['data_from']}→{payload['data_to']} "
            f"({payload['n_sel_days']}天) | 视图近{payload['months']}月 | "
            f"最新建议 {DEC_LABEL.get(dec, dec)} → {latest.get('rule_ui', '')}"
        )
        highlight = latest.get("asof_s") if latest else None
        self.chart.update_chart(payload, highlight=highlight)
        if highlight:
            self.asof_combo.setCurrentText(highlight)
            self.show_asof(highlight)

    def _on_asof_combo(self, text: str):
        if text:
            self.show_asof(text)

    def show_asof(self, asof_s: str):
        if not self._payload:
            return
        h = self._hist_by_asof.get(asof_s)
        if not h:
            return
        if self.asof_combo.currentText() != asof_s:
            self.asof_combo.blockSignals(True)
            self.asof_combo.setCurrentText(asof_s)
            self.asof_combo.blockSignals(False)
        self.chart.update_chart(self._payload, highlight=asof_s)

        daily: pd.DataFrame = self._payload.get("daily")
        day_row = None
        if daily is not None and not daily.empty:
            m = daily[daily["sel"].astype(str) == asof_s]
            if not m.empty:
                day_row = m.iloc[0]

        sc = h["scores"]
        day_exec = None
        day_exec_lab = "—"
        if day_row is not None:
            day_exec = day_row.get("exec_decision")
            day_exec_lab = DEC_LABEL.get(str(day_exec), str(day_exec or "—"))
        y, w = iso_year_week(date.fromisoformat(asof_s))
        lines = [
            f"asof = {asof_s}   ISO周 W{w} ({y})   评价窗 {h['window_from']} → {h['window_to']}  (window={self._payload['window']})",
            f"【本周执行】{day_exec_lab}   ← 上周最后选股日定下，本周共用",
            f"【当日建议】{DEC_LABEL.get(h['decision'], h['decision'])}   选股页若今天重算会勾: {h.get('rule_ui') or ''}",
            f"原因(当日建议): {h['reason']}",
            f"失效读法: {h['decay_hint']}",
            "",
            "【近窗得分】 day% / mean% / n",
        ]
        for key, lab in [
            ("core_ok", "CORE"),
            ("july_sat", "七月卫星"),
            ("june_sat", "六月卫星"),
            ("pack_july", "CORE+七月包"),
            ("pack_june", "CORE+六月包"),
            ("b_only", "仅B(对照)"),
            ("non_b", "非B(对照)"),
            ("baseline", "上MA10全样本"),
        ]:
            s = sc[key]
            lines.append(
                f"  {lab:12s}  day={_fmt_pct(s['day_mean']):>8s}  "
                f"mean={_fmt_pct(s['mean']):>8s}  n={s['n']:4d}"
            )

        lines.append("")
        lines.append("【当日日线回测票均】选股日实现（按票收益率pct）")
        lines.append("  执行包 = 上周最后选股日决策 → 本周整周共用（周频，无日频前瞻）")
        if day_row is not None:
            prev_dec = day_row.get("exec_decision")
            prev_lab = DEC_LABEL.get(str(prev_dec), str(prev_dec or "NA"))
            lines.append(
                f"  CORE      {_fmt_pct(day_row.get('core_ok'))}   "
                f"七月整包 {_fmt_pct(day_row.get('pack_july'))}   "
                f"六月整包 {_fmt_pct(day_row.get('pack_june'))}"
            )
            lines.append(
                f"  仅B       {_fmt_pct(day_row.get('b_only'))}   "
                f"非B  {_fmt_pct(day_row.get('non_b'))}   "
                f"上MA10 {_fmt_pct(day_row.get('baseline'))}"
            )
            lines.append(
                f"  执行包({prev_lab}) {_fmt_pct(day_row.get('exec_pack'))}   "
                f"七月卫星 {_fmt_pct(day_row.get('july_sat'))}   "
                f"六月卫星 {_fmt_pct(day_row.get('june_sat'))}"
            )
        else:
            lines.append("  (无当日按票样本)")

        basket = self._payload.get("basket")
        if basket is not None and not basket.empty:
            bm = basket[basket["sel"].astype(str) == asof_s]
            if not bm.empty:
                lines.append(
                    f"  组合总收益率%(汇总表，可能被现金摊薄): {_fmt_pct(bm.iloc[0]['basket_ret'])}"
                )

        lines.append("")
        lines.append("执行规则:")
        for r in h.get("rules") or []:
            lines.append(f"  - {r}")
        self.detail.setPlainText("\n".join(lines))


def main(argv=None):
    ap = argparse.ArgumentParser(description="MA10 风格切换监控 GUI")
    ap.add_argument("--auto-run", action="store_true", help="启动后自动刷新")
    ap.add_argument("--window", type=int, default=15)
    ap.add_argument("--months", type=int, default=3)
    ap.add_argument("--edge", type=float, default=rs.LEAD_EDGE)
    args, _ = ap.parse_known_args(argv)

    app = QApplication(sys.argv)
    dlg = Ma10RegimeMonitorDialog(
        auto_run=bool(args.auto_run),
        window=int(args.window),
        months=int(args.months),
        edge=float(args.edge),
    )
    dlg.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
