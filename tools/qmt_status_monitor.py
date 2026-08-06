# -*- coding: utf-8 -*-
"""
大 QMT 状态监控器 — 独立轻量窗口（默认只读；可写本地暂停/日线回填/量能待跑标志）。

用法:
  python tools/qmt_status_monitor.py
  python tools/qmt_status_monitor.py --interval 2 --top
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

# 保证项目根在 path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
os.chdir(_ROOT)

# 已解除告警保留条数（会话内）
_MAX_CLEARED_ALERTS = 30


def _card(title: str) -> tuple:
    from PyQt5.QtWidgets import QGroupBox, QVBoxLayout, QLabel
    box = QGroupBox(title)
    lay = QVBoxLayout(box)
    lab = QLabel("—")
    from PyQt5.QtCore import Qt

    lab.setWordWrap(True)
    lab.setTextInteractionFlags(Qt.TextSelectableByMouse)
    lay.addWidget(lab)
    return box, lab


def _fmt_clock(dt: Optional[datetime]) -> str:
    if not dt:
        return "—"
    return dt.strftime("%H:%M:%S")


def _escape_html(s: str) -> str:
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _normalize_trade_day(raw: str) -> Optional[str]:
    """YYYYMMDD 或 YYYY-MM-DD → YYYYMMDD；非法返回 None。"""
    s = str(raw or "").strip().replace("-", "").replace("/", "")
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        datetime.strptime(s, "%Y%m%d")
    except ValueError:
        return None
    return s


def _default_prev_trade_day() -> str:
    """上一个交易日（跳过周末；无日历时的轻量默认）。"""
    cur = date.today() - timedelta(days=1)
    for _ in range(10):
        if cur.weekday() < 5:
            return cur.strftime("%Y%m%d")
        cur -= timedelta(days=1)
    return (date.today() - timedelta(days=1)).strftime("%Y%m%d")


def _normalize_alert(a: Any) -> tuple:
    """返回 (id, text)。兼容旧版纯字符串告警。"""
    if isinstance(a, dict):
        text = str(a.get("text") or a.get("message") or "")
        aid = str(a.get("id") or a.get("key") or text)
        return aid, text
    text = str(a or "")
    return text, text


def _build_window_class():
    from PyQt5.QtCore import Qt, QTimer
    from PyQt5.QtGui import QFont
    from PyQt5.QtWidgets import (
        QCheckBox,
        QGridLayout,
        QGroupBox,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QMainWindow,
        QMessageBox,
        QProgressBar,
        QPushButton,
        QTableWidget,
        QTableWidgetItem,
        QVBoxLayout,
        QWidget,
        QTextEdit,
        QHeaderView,
    )
    from utils.qmt_status_snapshot import (
        AFTER_RANK_DIR,
        AFTER_RANK_REQ,
        DAILY_CACHE_DIR,
        DEFAULT_BACKFILL_START,
        FORCE_YEAR,
        RESET_FORCE_PROGRESS,
        TICK_FULL_DIR,
        TICK_PAUSE,
        build_snapshot,
        force_start_text,
        status_cn,
    )

    class QmtStatusMonitor(QMainWindow):
        def __init__(self, interval_ms: int = 2500, stay_on_top: bool = False):
            super().__init__()
            self.setWindowTitle("大 QMT 状态监控器")
            self.resize(780, 900)
            if stay_on_top:
                self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

            # id -> {text, first_seen, last_seen, cleared_at, status}
            self._alert_track: Dict[str, Dict[str, Any]] = {}

            root = QWidget()
            self.setCentralWidget(root)
            layout = QVBoxLayout(root)

            head = QHBoxLayout()
            self.lbl_online = QLabel("在线: —")
            self.lbl_online.setFont(QFont("Microsoft YaHei", 12, QFont.Bold))
            self.lbl_activity = QLabel("活动: —")
            self.lbl_activity.setWordWrap(True)
            self.lbl_refresh = QLabel("")
            self.chk_top = QCheckBox("置顶")
            self.chk_top.setChecked(stay_on_top)
            self.chk_top.toggled.connect(self._toggle_top)
            btn = QPushButton("立即刷新")
            btn.clicked.connect(self.refresh)
            head.addWidget(self.lbl_online)
            head.addWidget(self.lbl_activity, 1)
            head.addWidget(self.lbl_refresh)
            head.addWidget(self.chk_top)
            head.addWidget(btn)
            layout.addLayout(head)

            busy_box = QGroupBox("繁忙程度")
            busy_l = QVBoxLayout(busy_box)
            self.busy_bar = QProgressBar()
            self.busy_bar.setRange(0, 100)
            self.busy_bar.setTextVisible(True)
            self.lbl_busy = QLabel("")
            self.lbl_busy.setWordWrap(True)
            busy_l.addStretch(1)
            busy_l.addWidget(self.busy_bar)
            busy_l.addWidget(self.lbl_busy)
            busy_l.addStretch(1)

            self.alerts = QTextEdit()
            self.alerts.setReadOnly(True)
            self.alerts.setPlaceholderText("无告警")
            alert_box = self._wrap("告警（含首次/解除时间）", self.alerts)

            top_half = QGridLayout()
            top_half.addWidget(busy_box, 0, 0)
            top_half.addWidget(alert_box, 0, 1)
            top_half.setColumnStretch(0, 1)
            top_half.setColumnStretch(1, 1)
            # 左右同高，避免告警区单独撑高整窗
            busy_box.setMaximumHeight(130)
            alert_box.setMaximumHeight(130)
            layout.addLayout(top_half)

            comm_grid = QGridLayout()
            self.box_results, self.lab_results = _card("results.json（大QMT → 主程序）")
            self.box_rules, self.lab_rules = _card("rules_armed.json（主程序 → 大QMT）")
            self.lab_results.setMinimumHeight(88)
            self.lab_rules.setMinimumHeight(88)
            comm_grid.addWidget(self.box_results, 0, 0)
            comm_grid.addWidget(self.box_rules, 0, 1)
            comm_grid.setColumnStretch(0, 1)
            comm_grid.setColumnStretch(1, 1)
            layout.addLayout(comm_grid)

            grid = QGridLayout()
            # 左列：按需日线 + 盘后日线；右列：按需分时 + 盘后分时
            self.box_od_daily, self.lab_od_daily = _card("按需日线")
            self.box_daily, self.lab_daily = _card("盘后日线")
            daily_ctrl = QHBoxLayout()
            daily_ctrl.addWidget(QLabel("回填起始日"))
            self.edit_force_start = QLineEdit(DEFAULT_BACKFILL_START)
            self.edit_force_start.setPlaceholderText("YYYYMMDD 或 YYYY-MM-DD")
            self.edit_force_start.setMaximumWidth(130)
            self.edit_force_start.setToolTip(
                "回填拉取起点；默认 %s。仅影响本次 FORCE 拉取窗口，"
                "增量写盘不会裁掉更早已有 K 线。" % DEFAULT_BACKFILL_START
            )
            daily_ctrl.addWidget(self.edit_force_start)
            self.btn_force_year = QPushButton("启动回填")
            self.btn_force_year.setToolTip(
                "写入 data/daily_cache/FORCE_YEAR_BACKFILL（含起始日 JSON，需确认）\n"
                "存在时日线 runner 会按所选起始日做一次回填；完成后自动清除。"
            )
            self.btn_force_year.clicked.connect(self._toggle_force_year)
            self.btn_reset_force = QPushButton("重置回填进度")
            self.btn_reset_force.setToolTip(
                "创建 data/daily_cache/RESET_FORCE_PROGRESS（需确认）\n"
                "下次进入日线同步时丢弃中途检查点，从 0 重试回填。"
            )
            self.btn_reset_force.clicked.connect(self._request_reset_force_progress)
            daily_ctrl.addWidget(self.btn_force_year)
            daily_ctrl.addWidget(self.btn_reset_force)
            daily_ctrl.addStretch(1)
            self.box_daily.layout().addLayout(daily_ctrl)
            self.box_od_tick, self.lab_od_tick = _card("按需分时")
            self.box_tick_full, self.lab_tick_full = _card("盘后分时")
            self.btn_tick_pause = QPushButton("暂停")
            self.btn_tick_pause.setToolTip(
                "创建/删除 data/tick_full_sync/PAUSE（需确认）"
            )
            self.btn_tick_pause.clicked.connect(self._toggle_tick_pause)
            self.box_tick_full.layout().addWidget(self.btn_tick_pause)
            self.box_rank, self.lab_rank = _card("盘后量能")
            rank_ctrl = QHBoxLayout()
            rank_ctrl.addWidget(QLabel("目标交易日"))
            self.edit_rank_day = QLineEdit(_default_prev_trade_day())
            self.edit_rank_day.setPlaceholderText("YYYYMMDD 或 YYYY-MM-DD")
            self.edit_rank_day.setMaximumWidth(130)
            self.edit_rank_day.setToolTip("默认上一个交易日；格式 YYYYMMDD / YYYY-MM-DD")
            rank_ctrl.addWidget(self.edit_rank_day)
            self.chk_rank_force = QCheckBox("强制重跑")
            self.chk_rank_force.setToolTip(
                "勾选后忽略已有明细，并可跳过分时就绪等待"
            )
            rank_ctrl.addWidget(self.chk_rank_force)
            self.btn_rank_submit = QPushButton("提交待跑")
            self.btn_rank_submit.setToolTip(
                "写入 data/after_hours_rank/manual_request.json（需确认）"
            )
            self.btn_rank_submit.clicked.connect(self._submit_after_rank)
            self.btn_rank_cancel = QPushButton("取消待跑")
            self.btn_rank_cancel.setToolTip(
                "删除 data/after_hours_rank/manual_request.json（需确认）"
            )
            self.btn_rank_cancel.clicked.connect(self._cancel_after_rank)
            rank_ctrl.addWidget(self.btn_rank_submit)
            rank_ctrl.addWidget(self.btn_rank_cancel)
            rank_ctrl.addStretch(1)
            self.box_rank.layout().addLayout(rank_ctrl)
            grid.addWidget(self.box_od_daily, 0, 0)
            grid.addWidget(self.box_daily, 1, 0)
            grid.addWidget(self.box_od_tick, 0, 1)
            grid.addWidget(self.box_tick_full, 1, 1)
            grid.addWidget(self.box_rank, 2, 0, 1, 2)
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            layout.addLayout(grid)

            self.tbl_sched = QTableWidget(0, 3)
            self.tbl_sched.setHorizontalHeaderLabels(["任务", "时刻 / 规则", "说明"])
            self.tbl_sched.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            self.tbl_sched.setMinimumHeight(130)
            self.tbl_sched.setMaximumHeight(150)
            layout.addWidget(self._wrap("定时任务", self.tbl_sched))

            self.lbl_sched_meta = QLabel("")
            layout.addWidget(self.lbl_sched_meta)

            tip = QLabel(
                "默认只读监控：不向大 QMT 发指令"
                "（暂停标志 / 日线回填 / 盘后量能待跑仅写本地文件，由「蚂蚁量化规则」领取）。"
                "上方两框为通讯文件：results.json（行情/账户）与 rules_armed.json（规则/订阅）。"
                "预计剩余为粗算。关闭本窗口不影响交易/回测。"
            )
            tip.setStyleSheet("color:#666;")
            layout.addWidget(tip)

            self._timer = QTimer(self)
            self._timer.timeout.connect(self.refresh)
            self._timer.start(max(1000, int(interval_ms)))
            self.refresh()

        def _wrap(self, title: str, w):
            g = QGroupBox(title)
            l = QVBoxLayout(g)
            l.addWidget(w)
            return g

        def _toggle_top(self, on: bool) -> None:
            flags = self.windowFlags()
            if on:
                self.setWindowFlags(flags | Qt.WindowStaysOnTopHint)
            else:
                self.setWindowFlags(flags & ~Qt.WindowStaysOnTopHint)
            self.show()

        def _toggle_force_year(self) -> None:
            active = os.path.isfile(FORCE_YEAR)
            if active:
                reply = QMessageBox.question(
                    self,
                    "确认取消回填",
                    "将删除 data/daily_cache/FORCE_YEAR_BACKFILL，\n"
                    "后续日线同步不再做强制回填（增量照常）。\n\n"
                    "若正在回填中途，当前切片结束后即停止回填。\n\n"
                    "确定取消？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
                try:
                    os.remove(FORCE_YEAR)
                except OSError as e:
                    QMessageBox.warning(self, "失败", "删除回填标志失败：%s" % e)
                    return
            else:
                start = _normalize_trade_day(self.edit_force_start.text())
                if not start:
                    QMessageBox.warning(
                        self,
                        "日期无效",
                        "请输入有效回填起始日：YYYYMMDD 或 YYYY-MM-DD",
                    )
                    return
                reply = QMessageBox.question(
                    self,
                    "确认启动回填",
                    "将写入 data/daily_cache/FORCE_YEAR_BACKFILL：\n"
                    '  {"start":"%s", ...}\n\n'
                    "日线 runner 会从 %s 拉到当前同步交易日\n"
                    "（盘中以短切片推进，盘后可连续）。\n"
                    "完成后标志会自动清除。\n"
                    "增量写盘不会裁掉更早已有 K 线。\n\n"
                    "确定启动？"
                    % (start, force_start_text(start)),
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
                payload = {
                    "start": start,
                    "source": "qmt_status_monitor",
                    "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                }
                try:
                    os.makedirs(DAILY_CACHE_DIR, exist_ok=True)
                    tmp = FORCE_YEAR + ".tmp"
                    with open(tmp, "w", encoding="utf-8") as f:
                        json.dump(payload, f, ensure_ascii=False, indent=2)
                    os.replace(tmp, FORCE_YEAR)
                except OSError as e:
                    QMessageBox.warning(self, "失败", "创建回填标志失败：%s" % e)
                    return
            self.refresh()

        def _request_reset_force_progress(self) -> None:
            reply = QMessageBox.question(
                self,
                "确认重置回填进度",
                "将创建 data/daily_cache/RESET_FORCE_PROGRESS，\n"
                "下次日线同步入口会丢弃中途检查点（进度/成功/失败归零），\n"
                "并从 0 重新推进回填。\n\n"
                "标志会被 runner 自动消费删除。\n\n"
                "确定重置？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            try:
                os.makedirs(DAILY_CACHE_DIR, exist_ok=True)
                with open(RESET_FORCE_PROGRESS, "w", encoding="utf-8") as f:
                    f.write("")
            except OSError as e:
                QMessageBox.warning(self, "失败", "创建重置标志失败：%s" % e)
                return
            QMessageBox.information(
                self,
                "已提交",
                "重置标志已写入，等待大 QMT「蚂蚁量化规则」下次进入日线同步时生效。",
            )
            self.refresh()

        def _toggle_tick_pause(self) -> None:
            paused = os.path.isfile(TICK_PAUSE)
            if paused:
                reply = QMessageBox.question(
                    self,
                    "确认恢复",
                    "将删除 data/tick_full_sync/PAUSE，\n"
                    "盘后分时定时与手动队列将继续运行。\n\n确定恢复？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
                try:
                    os.remove(TICK_PAUSE)
                except OSError as e:
                    QMessageBox.warning(self, "失败", "删除暂停标志失败：%s" % e)
                    return
            else:
                reply = QMessageBox.question(
                    self,
                    "确认暂停",
                    "将创建 data/tick_full_sync/PAUSE，\n"
                    "盘后分时定时与手动队列将暂缓。\n\n确定暂停？",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.No,
                )
                if reply != QMessageBox.Yes:
                    return
                try:
                    os.makedirs(TICK_FULL_DIR, exist_ok=True)
                    with open(TICK_PAUSE, "w", encoding="utf-8") as f:
                        f.write("")
                except OSError as e:
                    QMessageBox.warning(self, "失败", "创建暂停标志失败：%s" % e)
                    return
            self.refresh()

        def _submit_after_rank(self) -> None:
            day = _normalize_trade_day(self.edit_rank_day.text())
            if not day:
                QMessageBox.warning(
                    self,
                    "日期无效",
                    "请输入有效交易日：YYYYMMDD 或 YYYY-MM-DD",
                )
                return
            force = bool(self.chk_rank_force.isChecked())
            overwrite = os.path.isfile(AFTER_RANK_REQ)
            lines = [
                "将写入 data/after_hours_rank/manual_request.json：",
                '  {"day":"%s","force":%s}' % (day, "true" if force else "false"),
                "",
                "任务会排队，由大 QMT「蚂蚁量化规则」periodic_sync 领取执行。",
            ]
            if force:
                lines.append(
                    "强制重跑：将忽略已有结果，并可能跳过分时就绪等待限制。"
                )
            if overwrite:
                lines.append("")
                lines.append("注意：已有待跑请求，提交将覆盖现有待跑。")
            lines.append("")
            lines.append("确定提交？")
            reply = QMessageBox.question(
                self,
                "确认提交盘后量能待跑",
                "\n".join(lines),
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            payload = {
                "day": day,
                "force": force,
                "source": "qmt_status_monitor",
                "updated_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            }
            try:
                os.makedirs(AFTER_RANK_DIR, exist_ok=True)
                tmp = AFTER_RANK_REQ + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(payload, f, ensure_ascii=False, indent=2)
                os.replace(tmp, AFTER_RANK_REQ)
            except OSError as e:
                QMessageBox.warning(self, "失败", "写入待跑失败：%s" % e)
                return
            self.refresh()

        def _cancel_after_rank(self) -> None:
            if not os.path.isfile(AFTER_RANK_REQ):
                QMessageBox.information(self, "无需取消", "当前没有盘后量能待跑请求。")
                return
            reply = QMessageBox.question(
                self,
                "确认取消盘后量能待跑",
                "将删除 data/after_hours_rank/manual_request.json，\n"
                "尚未被「蚂蚁量化规则」periodic_sync 领取的待跑将取消。\n\n"
                "确定取消？",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return
            try:
                os.remove(AFTER_RANK_REQ)
            except OSError as e:
                QMessageBox.warning(self, "失败", "删除待跑失败：%s" % e)
                return
            self.refresh()

        def _update_alert_track(self, raw_alerts: List[Any], now: datetime) -> None:
            active_ids = set()
            for a in raw_alerts or []:
                aid, text = _normalize_alert(a)
                if not aid:
                    continue
                active_ids.add(aid)
                prev = self._alert_track.get(aid)
                if prev and prev.get("status") == "告警中":
                    prev["last_seen"] = now
                    prev["text"] = text
                else:
                    # 新告警或已解除后再次触发
                    self._alert_track[aid] = {
                        "text": text,
                        "first_seen": now,
                        "last_seen": now,
                        "cleared_at": None,
                        "status": "告警中",
                    }

            for aid, st in list(self._alert_track.items()):
                if st.get("status") == "告警中" and aid not in active_ids:
                    st["status"] = "已解除"
                    st["cleared_at"] = now

            # 已解除过多时只保留最近 N 条
            cleared = [
                (aid, st)
                for aid, st in self._alert_track.items()
                if st.get("status") == "已解除"
            ]
            if len(cleared) > _MAX_CLEARED_ALERTS:
                cleared.sort(key=lambda x: x[1].get("cleared_at") or datetime.min)
                for aid, _ in cleared[: len(cleared) - _MAX_CLEARED_ALERTS]:
                    self._alert_track.pop(aid, None)

        def _render_alerts(self) -> None:
            if not self._alert_track:
                self.alerts.setPlainText("无告警")
                self.alerts.setStyleSheet("")
                return

            active = [
                (aid, st)
                for aid, st in self._alert_track.items()
                if st.get("status") == "告警中"
            ]
            cleared = [
                (aid, st)
                for aid, st in self._alert_track.items()
                if st.get("status") == "已解除"
            ]
            # 告警中按首次时间倒序；已解除按解除时间倒序
            active.sort(key=lambda x: x[1].get("first_seen") or datetime.min, reverse=True)
            cleared.sort(key=lambda x: x[1].get("cleared_at") or datetime.min, reverse=True)

            lines: List[str] = []
            for _, st in active:
                first = _fmt_clock(st.get("first_seen"))
                last = _fmt_clock(st.get("last_seen"))
                text = _escape_html(st.get("text") or "")
                time_bit = first if first == last else "%s～%s" % (first, last)
                lines.append(
                    '<span style="color:#a33;font-weight:bold;">[告警中]</span> '
                    '<span style="color:#822;">%s</span> %s'
                    % (_escape_html(time_bit), text)
                )
            for _, st in cleared:
                first = _fmt_clock(st.get("first_seen"))
                cleared_at = _fmt_clock(st.get("cleared_at"))
                text = _escape_html(st.get("text") or "")
                lines.append(
                    '<span style="color:#5a7;">[已解除]</span> '
                    '<span style="color:#666;">%s → %s</span> %s'
                    % (_escape_html(first), _escape_html(cleared_at), text)
                )

            self.alerts.setHtml("<br/>".join(lines))
            if active:
                self.alerts.setStyleSheet("background:#fff5f5;")
            else:
                self.alerts.setStyleSheet("background:#f6faf6;")

        def refresh(self) -> None:
            try:
                # 跨日清掉按需日线失败痕迹（只读快照本身不写盘）
                try:
                    from utils.data_sync_request import prune_stale_daily_failures

                    prune_stale_daily_failures()
                except Exception:
                    pass
                snap = build_snapshot()
            except Exception as e:
                self.lbl_activity.setText("刷新失败: %s" % e)
                return

            now = datetime.now()
            res = snap.get("results") or {}
            online = bool(res.get("online"))
            if online:
                self.lbl_online.setText("在线")
                self.lbl_online.setStyleSheet("color:#0a7; font-weight:bold; font-size:14px;")
            else:
                self.lbl_online.setText("离线")
                self.lbl_online.setStyleSheet("color:#c33; font-weight:bold; font-size:14px;")

            self.lbl_activity.setText("当前: %s" % (snap.get("activity") or "—"))
            self.lbl_refresh.setText("刷新 %s" % (snap.get("generated_at") or ""))

            busy = snap.get("busy") or {}
            score = int(busy.get("score") or 0)
            self.busy_bar.setValue(score)
            self.busy_bar.setFormat("%s  %%p%%" % (busy.get("label") or ""))
            level = busy.get("level") or "low"
            color = {
                "high": "#e67e22",
                "mid": "#2980b9",
                "low": "#27ae60",
                "offline": "#95a5a6",
            }.get(level, "#2980b9")
            self.busy_bar.setStyleSheet(
                "QProgressBar {border:1px solid #ccc; border-radius:4px; text-align:center;}"
                "QProgressBar::chunk {background:%s;}" % color
            )
            asset = res.get("total_asset")
            cash = res.get("cash")
            self.lbl_busy.setText(
                "模式=%s 交易日=%s 总资产=%s 现金=%s | 心跳年龄=%s"
                % (
                    res.get("mode") or "—",
                    res.get("trade_date") or "—",
                    ("%.2f" % asset) if isinstance(asset, (int, float)) else "—",
                    ("%.2f" % cash) if isinstance(cash, (int, float)) else "—",
                    ("%.0fs" % res["age_sec"]) if res.get("age_sec") is not None else "—",
                )
            )

            # 通讯文件：results.json / rules_armed.json
            age_r = res.get("age_sec")
            qlag = res.get("quote_lag_sec")
            self.lab_results.setText(
                "大小: %s\n"
                "更新: %s（心跳 %s）\n"
                "行情墙钟: %s%s\n"
                "行情 stocks: %s 只\n"
                "持仓: %s | 本地单: %s | 柜台单: %s\n"
                "已完成任务 id: %s"
                % (
                    res.get("size_text") or "—",
                    res.get("updated_at") or "—",
                    ("%.0fs前" % age_r) if age_r is not None else "—",
                    res.get("quotes_recv_at") or "—",
                    ("（停约%.0fs）" % qlag) if qlag is not None else "",
                    res.get("n_stocks") if res.get("exists") else "—",
                    res.get("n_positions") if res.get("exists") else "—",
                    res.get("n_orders") if res.get("exists") else "—",
                    res.get("n_broker_orders") if res.get("exists") else "—",
                    res.get("n_done_task_ids") if res.get("exists") else "—",
                )
            )
            ra = snap.get("rules_armed") or {}
            age_a = ra.get("age_sec")
            oe = ra.get("orders_enabled")
            oe_txt = "是" if oe is True else ("否" if oe is False else "—")
            pool_n = int(ra.get("n_pool_watch") or 0)
            pool_bit = "%s" % pool_n
            if pool_n >= 120:
                pool_bit = "%s ⚠偏大" % pool_n
            self.lab_rules.setText(
                "大小: %s\n"
                "更新: %s（%s）\n"
                "交易日: %s | 下单开关: %s\n"
                "武装任务: %s\n"
                "规则类型: %s\n"
                "watch: %s | 临时 pool: %s | 合计订阅: %s"
                % (
                    ra.get("size_text") or "—",
                    ra.get("updated_at") or "—",
                    ("%.0fs前" % age_a) if age_a is not None else ("缺失" if not ra.get("exists") else "—"),
                    ra.get("trade_date") or "—",
                    oe_txt,
                    ra.get("n_tasks") if ra.get("exists") else "—",
                    ra.get("rule_types_text") or "—",
                    ra.get("n_watch") if ra.get("exists") else "—",
                    pool_bit if ra.get("exists") else "—",
                    ra.get("n_subscribe") if ra.get("exists") else "—",
                )
            )

            self._update_alert_track(snap.get("alerts") or [], now)
            self._render_alerts()

            od = snap.get("ondemand") or {}
            self.lab_od_daily.setText(
                "待处理: %s\n失败: %s\n粗算预计剩余: %s"
                % (
                    od.get("daily_pending"),
                    od.get("daily_failed"),
                    od.get("daily_eta_text"),
                )
            )
            self.lab_od_tick.setText(
                "待处理: %s\n失败: %s\n粗算预计剩余: %s"
                % (
                    od.get("tick_pending"),
                    od.get("tick_failed"),
                    od.get("tick_eta_text"),
                )
            )

            tf = snap.get("tick_full") or {}
            prog = tf.get("progress") or {}
            paused = bool(tf.get("pause"))
            self.lab_tick_full.setText(
                "最近完成: %s\n"
                "已暂停: %s | 队列: %s | 当前日: %s\n"
                "进度 完成=%s 失败=%s | 更新: %s\n"
                "粗算预计剩余: %s"
                % (
                    tf.get("last_completed_text") or "—",
                    "是" if paused else "否",
                    ", ".join(tf.get("manual_days") or []) or "（空）",
                    tf.get("active_day") or "—",
                    prog.get("done") if prog else "—",
                    prog.get("fail") if prog else "—",
                    prog.get("updated_at") if prog else "—",
                    tf.get("eta_text") or "—",
                )
            )
            self.btn_tick_pause.setText("恢复" if paused else "暂停")

            dm = snap.get("daily_manifest") or {}
            force_on = bool(dm.get("force_year"))
            self.lab_daily.setText(
                "最近完成: %s\n"
                "状态: %s | 进度: %s / 全市场 %s\n"
                "成功=%s 失败=%s | 回填: %s\n"
                "回填起始: %s | 预计剩余: %s"
                % (
                    dm.get("last_completed_text") or "—",
                    dm.get("status_cn") or status_cn(dm.get("status")),
                    dm.get("progress"),
                    dm.get("universe_count"),
                    dm.get("ok_count"),
                    dm.get("fail_count"),
                    "是" if force_on else "否",
                    dm.get("force_start_text") or force_start_text(dm.get("force_start")),
                    dm.get("eta_text") or "—",
                )
            )
            self.btn_force_year.setText("取消回填" if force_on else "启动回填")
            # 回填进行中时同步输入框为当前 FORCE 起始日（便于查看）
            if force_on and dm.get("force_start"):
                cur = str(self.edit_force_start.text() or "").strip()
                if not _normalize_trade_day(cur):
                    self.edit_force_start.setText(str(dm.get("force_start")))
            if os.path.isfile(RESET_FORCE_PROGRESS):
                self.btn_reset_force.setText("重置已排队")
                self.btn_reset_force.setEnabled(False)
            else:
                self.btn_reset_force.setText("重置回填进度")
                self.btn_reset_force.setEnabled(True)

            ar = snap.get("after_rank") or {}
            pending = bool(ar.get("pending"))
            self.lab_rank.setText(
                "最近完成: %s\n待跑: %s\n目标交易日: %s\n强制重跑: %s"
                % (
                    ar.get("last_completed_text") or "—",
                    "是" if pending else "否",
                    ar.get("day") or "—",
                    "是" if ar.get("force") else "否",
                )
            )
            self.btn_rank_cancel.setEnabled(pending)

            sch = snap.get("schedule") or {}
            items = sch.get("items") or []
            self.tbl_sched.setRowCount(len(items))
            for i, it in enumerate(items):
                self.tbl_sched.setItem(i, 0, QTableWidgetItem(str(it.get("name") or "")))
                self.tbl_sched.setItem(i, 1, QTableWidgetItem(str(it.get("when") or "")))
                self.tbl_sched.setItem(i, 2, QTableWidgetItem(str(it.get("note") or "")))
            self.lbl_sched_meta.setText(
                "现在 %s | 工作日=%s | 盘中保护=%s | 今日日线槽 %s | 链式分笔约 %s"
                % (
                    sch.get("now"),
                    "是" if sch.get("is_weekday") else "否（周末）",
                    "生效" if sch.get("in_market_protect") else "否",
                    sch.get("today_daily_slot"),
                    sch.get("today_tick_chain_slot"),
                )
            )

    return QmtStatusMonitor


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="大 QMT 状态监控器")
    ap.add_argument("--interval", type=float, default=2.5, help="刷新间隔秒")
    ap.add_argument("--top", action="store_true", help="窗口置顶")
    ap.add_argument("--once", action="store_true", help="只打印快照到控制台后退出")
    args = ap.parse_args(argv)

    if args.once:
        from utils.qmt_status_snapshot import snapshot_summary_text

        print(snapshot_summary_text())
        return 0

    from PyQt5.QtWidgets import QApplication

    QmtStatusMonitor = _build_window_class()
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = QmtStatusMonitor(interval_ms=int(args.interval * 1000), stay_on_top=args.top)
    win.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
