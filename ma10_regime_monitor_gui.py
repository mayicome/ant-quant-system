#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
马总 MA10 · 风格切换监控 GUI（独立版）

数据来自 tools/ma10_regime_switch.py（与 CLI 同源）。

图上：
- 近 N 个月滚动决策：CORE / +七月风 / +六月风
- 近窗得分曲线：CORE、七月、六月、仅B、非B
- 日线回测收益：按交易开始日（当日剩余池开买）的整段收益率票均；另画全部入选（含未上MA10）作对照
- 量柱：开买成交只数（上MA10/未上MA10、仅B/非B 堆叠）
- 横轴为交易日，收到最近已收盘日（当天 15:00 前不含今天）
- 近窗 / 当日建议 / 本周执行：只用 asof 当时已了结的收益（卖完或到期清仓），无整段前视
- 交易开始日 = 昨日选股池 − 已触发后、当天实际开买（次日MA10 + 挂单窗1日）
- 点击图上某日 → 看 asof 详情与失效读法
- 图上右键 /「导出当日Excel」→ 当日剩余参与池(昨日选股−已触发) + 买卖/开买收益/近窗建议

用法:
  python ma10_regime_monitor_gui.py
  python ma10_regime_monitor_gui.py --auto-run
  python ma10_regime_monitor_gui.py --window 10 --months 3

界面「更新选股+回测」：缺口补新日 + 已有选股日近 2 天重跑
（规则：马总选股逻辑-次日MA10；entry_window=1；已有日尽量复用选股）；完成后点「刷新」。
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
    QDoubleSpinBox,
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
    """实盘周频：上周最后交易日的当日建议 → 本周每个交易日共用。

    当日建议已按 asof 时点过滤（当时已实现收益），因此本周执行不再偷看本周才走完的整段收益。
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


def daily_sleeve_means(df: pd.DataFrame) -> pd.DataFrame:
    """交易开始日 → 各袖套票均收益率%（当日剩余池实际开买）。"""
    return rs.trade_start_sleeve_means(df)


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
            df = rs.dedupe_by_trade_start(
                rs.drop_blacklist(rs.add_flags(rs.load_pool(files)))
            )
            completed_starts = sorted(x for x in df["start"].dropna().unique())
            if len(completed_starts) < self.window:
                raise RuntimeError(f"交易开始日不足 window={self.window}，仅 {len(completed_starts)} 天")

            last_close = rs.last_closed_trading_day()
            axis_all = rs.trading_days_inclusive(
                completed_starts[0], last_close, extra=list(completed_starts)
            )
            if len(axis_all) < self.window:
                raise RuntimeError(f"交易日不足 window={self.window}，仅 {len(axis_all)} 天")

            # 约 months 个自然月；横轴收到最近已收盘日
            cut = last_close - timedelta(days=int(self.months * 31))
            sels_view = [d for d in axis_all if d >= cut]
            # 滚动需要 window-1 天预热，从全历史取
            self.progress.emit("计算滚动风格决策…")
            hist: List[dict] = []
            for i in range(self.window - 1, len(axis_all)):
                asof = axis_all[i]
                if asof < cut:
                    continue
                win = axis_all[i - self.window + 1 : i + 1]
                sc = rs.score_window(df, win, asof=asof)
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
                        "n_pool": sc.get("n_pool"),
                        "n_known": sc.get("n_all"),
                        "n_unknown": sc.get("n_unknown"),
                    }
                )

            self.progress.emit("汇总交易开始日票均收益…")
            view_asofs = [h["asof"] for h in hist]
            daily = daily_sleeve_means(df)
            daily = pd.DataFrame({"sel": view_asofs}).merge(daily, on="sel", how="left")
            all_files = rs.discover_all_selected_files()
            if all_files:
                self.progress.emit("加载全部入选按票（含未上MA10，已排除黑名单）…")
                df_all = rs.dedupe_by_trade_start(
                    rs.drop_blacklist(
                        rs.add_flags(
                            rs.ensure_start(
                                rs.load_pool(all_files, filter_ma10=False, apply_known=False)
                            )
                        )
                    )
                )
                all_g = (
                    df_all.dropna(subset=["start", "ret"])
                    .groupby("start")["ret"]
                    .agg(["mean", "size"])
                    .reset_index()
                    .rename(columns={"start": "sel", "mean": "all_sel", "size": "all_sel_n_buy"})
                )
                daily = daily.merge(all_g, on="sel", how="left")
            else:
                daily["all_sel"] = None
                daily["all_sel_n_buy"] = None

            basket = pd.DataFrame(columns=["sel", "basket_ret"])

            # 执行包：上周末决策 → 本周每日票均（周频持有，与实盘一致；避免日频 T-1 高估切换）
            dec_map = {h["asof"]: h["decision"] for h in hist}
            exec_by_day = weekly_exec_decision_map(hist)
            for h in hist:
                h["exec_decision"] = exec_by_day.get(h["asof"])
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
                val = row[col] if col in row.index else None
                exec_rets.append(None if pd.isna(val) else val)
            daily = daily.copy()
            daily["exec_pack"] = exec_rets
            daily["exec_decision"] = exec_decisions  # 上周决策（本周执行）
            daily["decision"] = [dec_map.get(s) for s in daily["sel"]]  # 当日建议（色带）

            payload = {
                "files": [p.name for p in files],
                "data_from": str(completed_starts[0]),
                "data_to": str(completed_starts[-1]),
                "axis_to": str(last_close),
                "n_sel_days": len(completed_starts),
                "view_from": str(sels_view[0]) if sels_view else "",
                "view_to": str(sels_view[-1]) if sels_view else "",
                "window": self.window,
                "months": self.months,
                "edge": self.edge,
                "history": hist,
                "daily": daily,
                "basket": basket,
                "pool": df,
                "latest": hist[-1] if hist else None,
            }
            self.finished_ok.emit(payload)
        except Exception as e:
            self.failed.emit(str(e))


class PrepareDataWorker(QThread):
    """缺口补新日 + 近2日已有选股重跑回测（子进程）。"""

    progress = pyqtSignal(str)
    finished_ok = pyqtSignal(str)
    failed = pyqtSignal(str)

    def __init__(self, max_days: int = 15, parent=None):
        super().__init__(parent)
        self.max_days = int(max_days)

    def run(self):
        try:
            script = ROOT / "tools" / "prepare_ma10_regime_data.py"
            if not script.is_file():
                raise FileNotFoundError(f"缺少脚本: {script}")
            cmd = [
                sys.executable,
                "-X",
                "utf8",
                str(script),
                "--days",
                str(self.max_days),
            ]
            self.progress.emit(
                f"开始准备数据（缺口补新日 + 近2日已有选股重跑，上限 {self.max_days} 天）…"
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
                if len(last_lines) > 30:
                    last_lines = last_lines[-30:]
                if "无需补数" in s:
                    skipped = True
                self.progress.emit(s)
            rc = proc.wait()
            if rc != 0:
                tail = "\n".join(last_lines[-8:])
                raise RuntimeError(f"准备失败（退出码 {rc}）\n{tail}")
            if skipped:
                self.finished_ok.emit("无需补数：回测选股日已覆盖到最新收盘日，可直接「刷新」")
            else:
                self.finished_ok.emit("选股+回测完成（缺口+近2日重跑），可点「刷新」看图")
        except Exception as e:
            self.failed.emit(str(e))


class RegimeChartWidget(QWidget):
    point_clicked = pyqtSignal(str)
    export_requested = pyqtSignal(str)
    _FIG_W = 9.3
    _FIG_H = 5.8

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
        self._highlight_idx: int = 0
        self._cid = self.canvas.mpl_connect("button_press_event", self._on_click)
        self._sid = self.canvas.mpl_connect("scroll_event", self._on_scroll)
        self.canvas.setFocusPolicy(Qt.StrongFocus)
        self.setFocusPolicy(Qt.StrongFocus)

    def _finish_draw(self):
        self.canvas.setMinimumSize(0, 0)
        self.canvas.sync_figure_size()
        try:
            self.figure.tight_layout()
        except Exception:
            pass
        self.canvas.draw_idle()

    def _asof_at_event(self, event) -> Optional[str]:
        if not self._asofs:
            return None
        if event.inaxes is not None and event.xdata is not None:
            idx = int(round(event.xdata))
            idx = max(0, min(idx, len(self._asofs) - 1))
            return str(self._asofs[idx])
        if 0 <= self._highlight_idx < len(self._asofs):
            return str(self._asofs[self._highlight_idx])
        return None

    def _on_click(self, event):
        """左键：跳到鼠标所在日期；右键：弹出导出菜单。"""
        if not self._asofs:
            return
        if event.button == 3:
            asof_s = self._asof_at_event(event)
            if asof_s:
                self.point_clicked.emit(asof_s)
                self._popup_export_menu(asof_s, event)
            return
        if event.button != 1 or event.inaxes is None or event.xdata is None:
            return
        asof_s = self._asof_at_event(event)
        if not asof_s:
            return
        self.canvas.setFocus()
        self.point_clicked.emit(asof_s)

    def _popup_export_menu(self, asof_s: str, event=None):
        menu = QMenu(self)
        act = menu.addAction(f"导出 {asof_s} Excel…")
        chosen = menu.exec_(QCursor.pos())
        if chosen == act:
            self.export_requested.emit(asof_s)

    def _on_scroll(self, event):
        """滚轮：在日期间移动红线。"""
        if not self._asofs:
            return
        step = -1 if event.step > 0 else 1
        asof_s = self.step_highlight(step)
        if asof_s:
            self.point_clicked.emit(asof_s)

    def step_highlight(self, step: int) -> Optional[str]:
        if not self._asofs:
            return None
        idx = max(0, min(len(self._asofs) - 1, self._highlight_idx + step))
        if idx == self._highlight_idx:
            return None
        return str(self._asofs[idx])

    def clear(self, msg: str = "点击“刷新”加载近三个月数据"):
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.text(0.5, 0.5, msg, ha="center", va="center", transform=ax.transAxes, fontsize=12)
        ax.set_axis_off()
        self._finish_draw()
        self._asofs = []
        self._highlight_idx = 0

    def update_chart(self, payload: dict, highlight: Optional[str] = None):
        hist = payload.get("history") or []
        daily: pd.DataFrame = payload.get("daily")
        if not hist:
            self.clear("无滚动决策数据（交易开始日不足或窗口过大）")
            return

        asofs = [h["asof"] for h in hist]
        self._asofs = asofs
        x = np.arange(len(asofs))
        labels = [f"{d.month}/{d.day}" for d in asofs]

        self.figure.clear()
        gs = self.figure.add_gridspec(5, 1, height_ratios=[0.65, 0.65, 1.15, 1.25, 1.25], hspace=0.14)
        ax_exec = self.figure.add_subplot(gs[0])
        ax_sug = self.figure.add_subplot(gs[1], sharex=ax_exec)
        ax1 = self.figure.add_subplot(gs[2], sharex=ax_exec)
        ax2 = self.figure.add_subplot(gs[3], sharex=ax_exec)
        ax3 = self.figure.add_subplot(gs[4], sharex=ax_exec)

        ymap = {"CORE_ONLY": 0, "CORE_PLUS_JULY": 1, "CORE_PLUS_JUNE": 2}

        # --- 本周执行包（周频，看这个才是实盘在跑哪包）---
        exec_decisions: List[Optional[str]] = [h.get("exec_decision") for h in hist]

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
            f"(已完成开买 {payload['data_from']} → {payload['data_to']}  横轴→{payload.get('axis_to') or payload['view_to']})  |  "
            f"近窗=交易开始日当时已实现  上条=本周执行(上周定)  下条=当日建议  |  单击定位  ←→/滚轮移动红线",
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
            ax1.plot(x, vals, "-", lw=1.4, label=lab, color=color)
        ax1.axhline(0, color="#666", lw=0.8, ls="--")
        ax1.legend(loc="upper left", ncol=5, fontsize=8, framealpha=0.9)
        ax1.set_ylabel("当时已实现近窗日均%")
        ax1.grid(True, alpha=0.3)

        # --- 交易开始日票均：对照池 / 决策包 分两张 ---
        if daily is not None and not daily.empty:
            dmap = {row["sel"]: row for _, row in daily.iterrows()}

            def _series(col: str):
                return [dmap[a][col] if a in dmap else None for a in asofs]

            ax2.plot(
                x,
                _series("all_sel"),
                "--",
                lw=2.0,
                color="#6C3483",
                label="全部入选",
                zorder=4,
            )
            ax2.plot(
                x,
                _series("baseline"),
                "-",
                lw=1.8,
                color="#1B4F72",
                label="上MA10",
                zorder=3,
            )
            ax2.plot(x, _series("b_only"), "-", lw=1.2, color="#8E44AD", label="仅B")
            ax2.plot(x, _series("non_b"), "-", lw=1.2, color="#C0392B", label="非B")

            def _counts(col: str):
                vals = []
                for v in _series(col):
                    if v is None or (isinstance(v, float) and (np.isnan(v) or np.isinf(v))):
                        vals.append(0.0)
                    else:
                        try:
                            vals.append(float(v))
                        except Exception:
                            vals.append(0.0)
                return np.array(vals)

            n_all_buy = _counts("all_sel_n_buy")
            n_ma_buy = _counts("baseline_n")
            n_b_buy = _counts("b_only_n")
            n_non_b_buy = _counts("non_b_n")
            n_below_buy = np.maximum(n_all_buy - n_ma_buy, 0.0)
            ax2b = ax2.twinx()
            bw = 0.22
            off_l = -0.12
            off_r = 0.12
            edge = {"linewidth": 0.35, "edgecolor": "#333333"}

            def _stack_bars(xo, n_lo, n_hi, c_lo, c_hi, a_lo, a_hi, label_lo, label_hi):
                ax2b.bar(
                    x + xo,
                    n_lo,
                    width=bw,
                    color=c_lo,
                    alpha=a_lo,
                    label=label_lo,
                    zorder=0,
                    **edge,
                )
                ax2b.bar(
                    x + xo,
                    n_hi,
                    width=bw,
                    bottom=n_lo,
                    color=c_hi,
                    alpha=a_hi,
                    label=label_hi,
                    zorder=0,
                    **edge,
                )

            _stack_bars(
                off_l,
                n_ma_buy,
                n_below_buy,
                "#1A5276",
                "#AED6F1",
                0.5,
                0.7,
                "上MA10",
                "未上MA10",
            )
            _stack_bars(
                off_r,
                n_b_buy,
                n_non_b_buy,
                "#6C3483",
                "#F5B7B1",
                0.5,
                0.7,
                "仅B",
                "非B",
            )
            ax2b.set_ylabel("开买只数")
            ymax = float(
                np.nanmax(
                    [
                        np.nanmax(n_all_buy) if len(n_all_buy) else 0.0,
                        np.nanmax(n_ma_buy + n_below_buy) if len(n_ma_buy) else 0.0,
                        np.nanmax(n_b_buy + n_non_b_buy) if len(n_b_buy) else 0.0,
                    ]
                )
            )
            ax2b.set_ylim(0, max(ymax * 1.2, 1.0))
            ax2.set_zorder(ax2b.get_zorder() + 1)
            ax2.patch.set_visible(False)
            h1, l1 = ax2.get_legend_handles_labels()
            h2, l2 = ax2b.get_legend_handles_labels()
            ax2.legend(h1 + h2, l1 + l2, loc="upper left", ncol=4, fontsize=7, framealpha=0.9)

            ax3.plot(x, _series("core_ok"), "-", lw=1.3, color="#2E86AB", label="CORE")
            ax3.plot(x, _series("pack_july"), "-", lw=1.3, color="#E67E22", label="七月整包")
            ax3.plot(x, _series("pack_june"), "-", lw=1.3, color="#27AE60", label="六月整包")
            mk = max(1, len(x) // 25)
            ax3.plot(
                x,
                _series("exec_pack"),
                "--o",
                ms=5,
                lw=1.5,
                color="#1A5276",
                markevery=mk,
                label="执行包(上周→本周)",
                zorder=5,
            )
            n_core = _counts("core_ok_n")
            n_pj = _counts("pack_july_n")
            n_pn = _counts("pack_june_n")
            ax3b = ax3.twinx()
            bw3 = 0.24
            edge3 = {"linewidth": 0.35, "edgecolor": "#333333"}
            ax3b.bar(
                x - bw3,
                n_core,
                width=bw3,
                color="#2E86AB",
                alpha=0.45,
                label="CORE只数",
                zorder=0,
                **edge3,
            )
            ax3b.bar(
                x,
                n_pj,
                width=bw3,
                color="#E67E22",
                alpha=0.45,
                label="七月整包只数",
                zorder=0,
                **edge3,
            )
            ax3b.bar(
                x + bw3,
                n_pn,
                width=bw3,
                color="#27AE60",
                alpha=0.45,
                label="六月整包只数",
                zorder=0,
                **edge3,
            )
            ax3b.set_ylabel("只数")
            ymax3 = float(
                np.nanmax(
                    [
                        np.nanmax(n_core) if len(n_core) else 0.0,
                        np.nanmax(n_pj) if len(n_pj) else 0.0,
                        np.nanmax(n_pn) if len(n_pn) else 0.0,
                    ]
                )
            )
            ax3b.set_ylim(0, max(ymax3 * 1.25, 1.0))
            ax3.set_zorder(ax3b.get_zorder() + 1)
            ax3.patch.set_visible(False)
            h3a, l3a = ax3.get_legend_handles_labels()
            h3b, l3b = ax3b.get_legend_handles_labels()
            ax3.legend(h3a + h3b, l3a + l3b, loc="upper left", ncol=4, fontsize=7, framealpha=0.9)
        last_wk2 = None
        for i, a in enumerate(asofs):
            wk = iso_year_week(a)
            if wk != last_wk2:
                if i > 0:
                    ax1.axvline(i - 0.5, color="#333", lw=0.8, ls=":", alpha=0.4)
                    ax2.axvline(i - 0.5, color="#333", lw=0.8, ls=":", alpha=0.4)
                    ax3.axvline(i - 0.5, color="#333", lw=0.8, ls=":", alpha=0.4)
                last_wk2 = wk
        ax2.axhline(0, color="#666", lw=0.8, ls="--")
        ax2.set_ylabel("交易开始日票均%\n对照；柱=开买只数")
        ax2.grid(True, axis="y", alpha=0.3)
        ax3.axhline(0, color="#666", lw=0.8, ls="--")
        if daily is None or daily.empty:
            ax3.legend(loc="upper left", ncol=4, fontsize=8, framealpha=0.9)
        ax3.set_ylabel("交易开始日票均%\n决策包")
        ax3.grid(True, alpha=0.3)

        # x labels
        step = max(1, len(labels) // 12)
        ax3.set_xticks(x[::step])
        ax3.set_xticklabels([labels[i] for i in range(0, len(labels), step)], rotation=0)
        for ax in (ax_exec, ax_sug, ax1, ax2):
            plt.setp(ax.get_xticklabels(), visible=False)

        self._highlight_idx = max(0, len(asofs) - 1)
        if highlight:
            try:
                hi = date.fromisoformat(highlight)
                if hi in asofs:
                    ix = asofs.index(hi)
                    self._highlight_idx = ix
                    for ax in (ax_exec, ax_sug, ax1, ax2, ax3):
                        ax.axvline(ix, color="#E74C3C", lw=1.2, alpha=0.8)
            except Exception:
                pass

        self._finish_draw()


class Ma10RegimeMonitorDialog(QDialog):
    def __init__(
        self,
        parent=None,
        auto_run: bool = False,
        window: int = 10,
        months: int = 3,
        edge: float = rs.LEAD_EDGE,
    ):
        super().__init__(parent)
        self.setWindowTitle("蚂蚁量化 - MA10风格切换监控")
        self.setWindowFlags(
            self.windowFlags()
            | Qt.WindowMaximizeButtonHint
            | Qt.WindowMinimizeButtonHint
        )
        self._auto_run = bool(auto_run)
        self._worker: Optional[MonitorWorker] = None
        self._prepare_worker: Optional[PrepareDataWorker] = None
        self._payload: Optional[dict] = None
        self._hist_by_asof: Dict[str, dict] = {}
        self._prepare_max_days = 15

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
        self.window_spin.setRange(5, 60)
        self.window_spin.setValue(int(window))
        top.addWidget(self.window_spin)

        top.addWidget(QLabel("月份:"))
        self.months_spin = QSpinBox()
        self.months_spin.setRange(1, 36)
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

        self.prepare_btn = QPushButton("更新选股+回测")
        self.prepare_btn.setToolTip(
            "按回测按票补缺口，并重跑最近 2 个已有选股日"
            f"（规则=次日MA10；挂单窗=1；上限 {self._prepare_max_days} 天）；"
            "完成后请点「刷新」"
        )
        self.prepare_btn.clicked.connect(self.prepare_data)
        top.addWidget(self.prepare_btn)

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.clicked.connect(self.refresh)
        top.addWidget(self.refresh_btn)

        self.export_btn = QPushButton("导出当日Excel")
        self.export_btn.setToolTip(
            "导出当前 asof：当日剩余参与池(昨日选股−已触发)、买卖、开买收益、近窗建议（也可在图上右键）"
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

        self.chart = RegimeChartWidget()
        self.chart.point_clicked.connect(self.show_asof)
        self.chart.export_requested.connect(self.export_asof)
        split.addWidget(self.chart)

        self.detail = QTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMinimumHeight(72)
        self.detail.setMaximumHeight(96)
        _f = QFont("Consolas")
        if _f.pointSize() <= 0:
            _f.setPointSize(10)
        else:
            _f.setPointSize(max(9, _f.pointSize()))
        # 中文环境用微软雅黑更稳
        self.detail.setFont(QFont("Microsoft YaHei", 10))
        split.addWidget(self.detail)
        split.setStretchFactor(0, 8)
        split.setStretchFactor(1, 1)

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
        if not self._payload:
            return
        asof_s = self.chart.step_highlight(step)
        if asof_s:
            self.show_asof(asof_s)

    def _open_dir(self):
        path = str(rs.DIR)
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
            QMessageBox.information(self, "导出", "请先刷新并定位一个 asof 日期。")
            return
        self.export_asof(asof_s)

    def export_asof(self, asof_s: str):
        if not self._payload:
            QMessageBox.information(self, "导出", "请先点击「刷新」加载数据。")
            return
        asof_s = (asof_s or "").strip()
        h = self._hist_by_asof.get(asof_s)
        if not h:
            QMessageBox.warning(self, "导出", f"找不到 asof={asof_s} 的近窗记录。")
            return
        try:
            asof_d = date.fromisoformat(asof_s)
        except Exception:
            QMessageBox.warning(self, "导出", f"日期无效: {asof_s}")
            return

        default_name = f"MA10风格切换_{asof_s}.xlsx"
        default_path = str(rs.DIR / default_name)
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

        daily_row = None
        daily = self._payload.get("daily")
        if isinstance(daily, pd.DataFrame) and not daily.empty and "sel" in daily.columns:
            m = daily[daily["sel"].astype(str) == asof_s]
            if not m.empty:
                daily_row = m.iloc[0]

        self.status_label.setText(f"正在导出 {asof_s} …")
        QApplication.processEvents()
        try:
            out = rs.export_asof_report(
                asof_d,
                Path(path),
                hist_row=h,
                daily_row=daily_row,
                window=int(self._payload.get("window") or self.window_spin.value()),
                pool=self._payload.get("pool"),
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
            f"准备数据中（按回测选股日缺口自动补，上限 {self._prepare_max_days} 天）…"
        )
        self._prepare_worker = PrepareDataWorker(max_days=self._prepare_max_days, parent=self)
        self._prepare_worker.progress.connect(self._on_prepare_progress)
        self._prepare_worker.finished_ok.connect(self._on_prepare_ok)
        self._prepare_worker.failed.connect(self._on_prepare_fail)
        self._prepare_worker.start()

    def _on_prepare_progress(self, msg: str):
        tip = (msg or "").strip()
        if len(tip) > 120:
            tip = tip[:117] + "…"
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
        self.prepare_btn.setEnabled(True)
        self.status_label.setText("失败")
        self.chart.clear("加载失败")
        QMessageBox.warning(self, "加载失败", err)

    def _on_ok(self, payload: dict):
        self.refresh_btn.setEnabled(True)
        self.prepare_btn.setEnabled(True)
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
        try:
            from utils.monitor_stock_type_filter import summarize_config

            type_tip = " | " + summarize_config()
        except Exception:
            type_tip = ""
        self.status_label.setText(
            f"已加载 {n_files} 个按票文件 | 横轴 {payload['view_from']}→{payload.get('axis_to') or payload['view_to']} "
            f"| 已完成开买 {payload['data_from']}→{payload['data_to']} ({payload['n_sel_days']}天) "
            f"| 视图近{payload['months']}月 | "
            f"最新建议 {DEC_LABEL.get(dec, dec)} → {latest.get('rule_ui', '')}"
            f"{type_tip}"
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
        day_exec = h.get("exec_decision")
        day_exec_lab = DEC_LABEL.get(str(day_exec), str(day_exec or "—")) if day_exec else "—"
        asof_d = date.fromisoformat(asof_s)
        y, w = iso_year_week(asof_d)
        weekdays = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
        weekday = weekdays[asof_d.weekday()]
        lines = [
            f"asof = {asof_s} {weekday}   ISO周 W{w} ({y})   评价窗 {h['window_from']} → {h['window_to']}  (window={self._payload['window']})",
            f"【本周执行】{day_exec_lab}   ← 上周最后交易日定下，本周共用（该建议只用当时已实现收益）",
            f"【当日建议】{DEC_LABEL.get(h['decision'], h['decision'])}   选股页若今天重算会勾: {h.get('rule_ui') or ''}",
            f"原因(当日建议): {h['reason']}",
            f"失效读法: {h['decay_hint']}",
            "",
            "【近窗得分】 day% / mean% / n   （按交易开始日；实际了结日 ≤ asof，未卖完不计入）",
        ]
        n_pool, n_known, n_unk = h.get("n_pool"), h.get("n_known"), h.get("n_unknown")
        if n_pool is not None:
            lines.append(f"  窗内 {n_pool} 票，当时已知 {n_known}，尚未实现 {n_unk}")
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
        lines.append("【开买成交只数】交易开始日实际买入")
        if day_row is not None:
            n_all_b = day_row.get("all_sel_n_buy")
            n_ma_b = day_row.get("baseline_n")
            n_b_b = day_row.get("b_only_n")
            n_nb_b = day_row.get("non_b_n")
            if any(pd.notna(x) for x in (n_all_b, n_ma_b, n_b_b, n_nb_b)):
                n_below_b = (
                    int(n_all_b) - int(n_ma_b)
                    if pd.notna(n_all_b) and pd.notna(n_ma_b)
                    else None
                )
                lines.append(
                    f"  全部成交 {int(n_all_b) if pd.notna(n_all_b) else '—'}   "
                    f"上MA10 {int(n_ma_b) if pd.notna(n_ma_b) else '—'}   "
                    f"未上MA10 {n_below_b if n_below_b is not None else '—'}"
                )
                lines.append(
                    f"  仅B {int(n_b_b) if pd.notna(n_b_b) else '—'}   "
                    f"非B {int(n_nb_b) if pd.notna(n_nb_b) else '—'}"
                )
            else:
                lines.append("  （该日尚无开买成交）")
        else:
            lines.append("  （无当日数据）")

        lines.append("")
        lines.append("【交易开始日票均】昨日选股池 − 已触发后、当天实际开买的票，整段收益率等权平均")
        lines.append(
            f"  近窗 = 最近 {self._payload['window']} 个交易开始日这些日均再平均；尚未了结的票不进近窗"
        )
        lines.append("  两天前的日均基本冻住，只有最近两天还会随未完成样本变化")
        lines.append("  执行包 = 上周定下的包，在该交易开始日上的票均")
        has_ret = day_row is not None and (
            not pd.isna(day_row.get("baseline")) or not pd.isna(day_row.get("all_sel"))
        )
        if has_ret:
            prev_dec = day_row.get("exec_decision") or h.get("exec_decision")
            prev_lab = DEC_LABEL.get(str(prev_dec), str(prev_dec or "NA"))
            n_base = day_row.get("baseline_n")
            n_txt = f"  n={int(n_base)}" if pd.notna(n_base) else ""
            n_all_buy = day_row.get("all_sel_n_buy")
            n_all_txt = f" n={int(n_all_buy)}" if pd.notna(n_all_buy) else ""
            lines.append(
                f"  全部入选  {_fmt_pct(day_row.get('all_sel'))}{n_all_txt}   "
                f"上MA10 {_fmt_pct(day_row.get('baseline'))}{n_txt}"
            )
            lines.append(
                f"  CORE      {_fmt_pct(day_row.get('core_ok'))}   "
                f"七月整包 {_fmt_pct(day_row.get('pack_july'))}   "
                f"六月整包 {_fmt_pct(day_row.get('pack_june'))}"
            )
            n_b = day_row.get("b_only_n")
            n_nb = day_row.get("non_b_n")
            if n_b is None or (isinstance(n_b, float) and pd.isna(n_b)):
                n_b = None
            n_b_txt = f" n={int(n_b)}" if n_b is not None and pd.notna(n_b) else ""
            n_nb_txt = f" n={int(n_nb)}" if n_nb is not None and pd.notna(n_nb) else ""
            lines.append(
                f"  仅B       {_fmt_pct(day_row.get('b_only'))}{n_b_txt}   "
                f"非B  {_fmt_pct(day_row.get('non_b'))}{n_nb_txt}"
            )
            lines.append("  （票均行的 n=开买成交只数；量柱同口径）")
            lines.append(
                f"  执行包({prev_lab}) {_fmt_pct(day_row.get('exec_pack'))}   "
                f"七月卫星 {_fmt_pct(day_row.get('july_sat'))}   "
                f"六月卫星 {_fmt_pct(day_row.get('june_sat'))}"
            )
        else:
            lines.append("  (该交易开始日尚无已完成开买样本)")

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
    ap.add_argument("--window", type=int, default=10)
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
