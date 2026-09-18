# -*- coding: utf-8 -*-
"""盘后批跑 GUI。

手动（默认，直接运行本文件）：
  - 不因「今天非交易日」跳过；目标日 = 最近一个交易日
  - 先展示各数据是否齐全，再选「全部重新获取」或勾选单项重跑

定时（计划任务 bat 传 --scheduled）：
  - 非交易日则提示跳过并挂起至次日 09:00
  - 交易日自动顺序跑全流程

未手动关闭时，次日 09:00 自动退出。
"""
from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Set

from PyQt5.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt5.QtGui import QColor, QFont
from PyQt5.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
os.chdir(ROOT)

from utils.post_market_completeness import check_all  # noqa: E402
from utils.post_market_pipeline import (  # noqa: E402
    PipelineStep,
    build_step_catalog,
    project_root,
    resolve_asof_day,
    run_step,
)


STATUS_PENDING = "等待"
STATUS_RUNNING = "进行中"
STATUS_OK = "成功"
STATUS_FAIL = "失败"
STATUS_SKIP = "跳过"
STATUS_RETRY = "重试中"

COL_CHECK, COL_NAME, COL_READY, COL_FINISHED, COL_STATUS, COL_DETAIL = range(6)


class Worker(QThread):
    step_started = pyqtSignal(str)
    step_finished = pyqtSignal(str, bool, int, str)  # id, ok, code, detail
    log_line = pyqtSignal(str)
    all_done = pyqtSignal()

    def __init__(
        self,
        step_ids: List[str],
        *,
        asof: date,
        live_only: bool,
        use_asof_freeze: bool,
        stop_on_gate_fail: bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.step_ids = list(step_ids)
        self.asof = asof
        self.live_only = live_only
        self.use_asof_freeze = use_asof_freeze
        self.stop_on_gate_fail = stop_on_gate_fail
        self._stop_wechat = False
        self._abort = False

    def request_stop_wechat_retry(self) -> None:
        self._stop_wechat = True

    def request_abort(self) -> None:
        self._abort = True

    def run(self) -> None:
        root = project_root()
        gate_ids = {"daily_cache"}
        for sid in self.step_ids:
            if self._abort:
                break
            self.step_started.emit(sid)
            self.log_line.emit(f">>> 开始 {sid}")
            try:
                result = run_step(
                    sid,
                    script_dir=root,
                    asof=self.asof,
                    live_only=self.live_only,
                    use_asof_freeze=self.use_asof_freeze,
                    wechat_auto_retry=True,
                    stop_wechat_retry=lambda: self._stop_wechat,
                    sleep_between=1.0,
                )
                self.step_finished.emit(
                    sid, result.ok, result.exit_code, result.detail or ""
                )
                self.log_line.emit(
                    f"<<< 结束 {sid} ok={result.ok} code={result.exit_code} {result.detail}"
                )
                if (
                    self.stop_on_gate_fail
                    and sid in gate_ids
                    and not result.ok
                    and not result.skipped
                ):
                    self.log_line.emit("[中止] 日线门禁失败，后续自动步骤不再执行")
                    break
            except Exception as e:
                self.step_finished.emit(sid, False, 1, str(e))
                self.log_line.emit(f"<<< 异常 {sid}: {e}\n{traceback.format_exc()}")
        self.all_done.emit()


class PostMarketWindow(QMainWindow):
    def __init__(self, *, scheduled: bool = False, live_only: bool = False):
        super().__init__()
        self.scheduled = scheduled
        self.live_only = live_only
        self.today = date.today()
        self.asof = self.today if scheduled else resolve_asof_day()
        self.steps: List[PipelineStep] = build_step_catalog(live_only=live_only)
        self.step_by_id: Dict[str, PipelineStep] = {s.id: s for s in self.steps}
        self.row_by_id: Dict[str, int] = {}
        self.status_by_id: Dict[str, str] = {}
        self.worker: Optional[Worker] = None
        self._pipeline_busy = False
        self._close_at = datetime.combine(
            self.today + timedelta(days=1), datetime.strptime("09:00", "%H:%M").time()
        )

        mode = "定时" if scheduled else "手动"
        tag = "仅实盘" if live_only else "全量"
        self.setWindowTitle(f"盘后批跑 · {mode} · {tag} · 目标日 {self.asof}")
        self.resize(980, 720)
        self._build_ui()
        self._refresh_completeness()

        self._close_timer = QTimer(self)
        self._close_timer.setInterval(30_000)
        self._close_timer.timeout.connect(self._tick_auto_close)
        self._close_timer.start()
        self._tick_auto_close()

        if scheduled:
            QTimer.singleShot(400, self._start_scheduled)
        else:
            self._set_phase_manual_select()

    def _build_ui(self) -> None:
        central = QWidget(self)
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        self.hdr = QLabel()
        self.hdr.setWordWrap(True)
        font = QFont()
        font.setPointSize(10)
        self.hdr.setFont(font)
        layout.addWidget(self.hdr)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["重跑", "步骤", "数据齐全", "最近完成", "运行状态", "说明"]
        )
        self.table.horizontalHeader().setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(COL_DETAIL, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        self.btn_rerun_all = QPushButton("全部重新获取")
        self.btn_rerun_all.clicked.connect(self._on_rerun_all)
        self.btn_rerun_sel = QPushButton("仅重新获取勾选项")
        self.btn_rerun_sel.clicked.connect(self._on_rerun_selected)
        self.btn_refresh = QPushButton("刷新齐全状态")
        self.btn_refresh.clicked.connect(self._refresh_completeness)
        self.btn_continue = QPushButton("从失败门禁后继续")
        self.btn_continue.clicked.connect(self._on_continue_after_gate)
        self.btn_continue.setEnabled(False)
        self.btn_stop_wechat = QPushButton("停止公众号自动重试")
        self.btn_stop_wechat.clicked.connect(self._on_stop_wechat)
        self.btn_stop_wechat.setEnabled(False)
        self.btn_retry_fail = QPushButton("重跑当前失败行")
        self.btn_retry_fail.clicked.connect(self._on_retry_failed_row)
        btn_row.addWidget(self.btn_rerun_all)
        btn_row.addWidget(self.btn_rerun_sel)
        btn_row.addWidget(self.btn_refresh)
        btn_row.addWidget(self.btn_continue)
        btn_row.addWidget(self.btn_retry_fail)
        btn_row.addWidget(self.btn_stop_wechat)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumHeight(180)
        layout.addWidget(self.log)

        self.setStatusBar(QStatusBar())
        self._fill_table_rows()
        self._update_header()

    def _update_header(self) -> None:
        mode = "定时自动" if self.scheduled else "手动补跑"
        asof_hint = ""
        if not self.scheduled:
            asof_hint = (
                "（非交易日或交易日 15:00 前 → 上一交易日；"
                "交易日 15:00 后 → 当天）"
            )
        live_src = ""
        if self.live_only:
            live_src = " · 已检测到实盘标记（data/qmt_live_only.flag / 环境变量）"
        self.hdr.setText(
            f"<b>{mode}</b>　今天 {self.today}　"
            f"<b>目标交易日 {self.asof}</b>{asof_hint}　"
            f"<b>{'仅实盘' if self.live_only else '全量'}</b>{live_src}<br>"
            f"未手动关闭时将于 <b>{self._close_at.strftime('%Y-%m-%d %H:%M')}</b> 自动退出。"
        )

    def _fill_table_rows(self) -> None:
        if self.scheduled:
            show = [s for s in self.steps if s.id != "final_notify"]
        else:
            show = [s for s in self.steps if s.selectable]
        self.table.setRowCount(len(show))
        self.row_by_id.clear()
        for i, step in enumerate(show):
            self.row_by_id[step.id] = i
            self.status_by_id[step.id] = STATUS_PENDING

            chk = QTableWidgetItem()
            flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled
            if not self.scheduled and step.selectable:
                flags |= Qt.ItemIsUserCheckable
                chk.setFlags(flags)
                chk.setCheckState(Qt.Unchecked)
            else:
                chk.setFlags(flags)
            self.table.setItem(i, COL_CHECK, chk)

            self.table.setItem(i, COL_NAME, QTableWidgetItem(step.name))
            self.table.setItem(i, COL_READY, QTableWidgetItem("-"))
            self.table.setItem(i, COL_FINISHED, QTableWidgetItem("-"))
            self.table.setItem(i, COL_STATUS, QTableWidgetItem(STATUS_PENDING))
            self.table.setItem(i, COL_DETAIL, QTableWidgetItem(""))

    def _set_phase_manual_select(self) -> None:
        self.btn_rerun_all.setEnabled(True)
        self.btn_rerun_sel.setEnabled(True)
        self.btn_refresh.setEnabled(True)
        self._append_log(
            f"手动模式：目标日={self.asof}（最近交易日）。"
            "请查看齐全状态后选择「全部重新获取」或勾选后「仅重新获取勾选项」。"
        )

    def _refresh_completeness(self) -> None:
        ids = list(self.row_by_id.keys())
        results = {r["id"]: r for r in check_all(self.asof, ids)}
        for sid, row in self.row_by_id.items():
            r = results.get(sid) or {"ok": False, "detail": "无结果", "finished_at": ""}
            ok = bool(r.get("ok"))
            ready_item = QTableWidgetItem("齐全" if ok else "缺")
            ready_item.setForeground(QColor("#1b5e20" if ok else "#b71c1c"))
            self.table.setItem(row, COL_READY, ready_item)
            finished = str(r.get("finished_at") or "").strip() or "-"
            self.table.setItem(row, COL_FINISHED, QTableWidgetItem(finished))
            self.table.setItem(row, COL_DETAIL, QTableWidgetItem(str(r.get("detail") or "")))
            chk = self.table.item(row, COL_CHECK)
            step = self.step_by_id.get(sid)
            if chk and step and step.selectable and not self.scheduled:
                # 缺的默认勾上，齐全的默认不勾
                chk.setCheckState(Qt.Unchecked if ok else Qt.Checked)

    def _append_log(self, text: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.log.append(f"[{ts}] {text}")

    def _set_status(self, sid: str, status: str, detail: str = "") -> None:
        self.status_by_id[sid] = status
        row = self.row_by_id.get(sid)
        if row is None:
            return
        item = QTableWidgetItem(status)
        color = {
            STATUS_OK: "#1b5e20",
            STATUS_FAIL: "#b71c1c",
            STATUS_RUNNING: "#0d47a1",
            STATUS_RETRY: "#e65100",
            STATUS_SKIP: "#616161",
        }.get(status)
        if color:
            item.setForeground(QColor(color))
        self.table.setItem(row, COL_STATUS, item)
        if detail:
            self.table.setItem(row, COL_DETAIL, QTableWidgetItem(detail))

    def _set_controls_busy(self, busy: bool) -> None:
        self._pipeline_busy = busy
        self.btn_rerun_all.setEnabled(not busy and not self.scheduled)
        self.btn_rerun_sel.setEnabled(not busy and not self.scheduled)
        self.btn_refresh.setEnabled(not busy)
        self.btn_retry_fail.setEnabled(not busy)
        self.btn_stop_wechat.setEnabled(busy)

    def _start_worker(
        self,
        step_ids: List[str],
        *,
        use_asof_freeze: bool,
        stop_on_gate_fail: bool,
    ) -> None:
        if self._pipeline_busy:
            QMessageBox.information(self, "批跑", "已有任务在执行。")
            return
        if not step_ids:
            QMessageBox.information(self, "批跑", "没有要执行的步骤。")
            return
        self._set_controls_busy(True)
        for sid in step_ids:
            self._set_status(sid, STATUS_PENDING)
        self.worker = Worker(
            step_ids,
            asof=self.asof,
            live_only=self.live_only,
            use_asof_freeze=use_asof_freeze,
            stop_on_gate_fail=stop_on_gate_fail,
            parent=self,
        )
        self.worker.step_started.connect(self._on_step_started)
        self.worker.step_finished.connect(self._on_step_finished)
        self.worker.log_line.connect(self._append_log)
        self.worker.all_done.connect(self._on_all_done)
        self.worker.start()

    def _on_step_started(self, sid: str) -> None:
        if sid == "wechat_draft":
            self._set_status(sid, STATUS_RETRY, "执行中（白名单失败会自动重试）")
        else:
            self._set_status(sid, STATUS_RUNNING)

    def _on_step_finished(self, sid: str, ok: bool, code: int, detail: str) -> None:
        self._set_status(
            sid,
            STATUS_OK if ok else STATUS_FAIL,
            detail or f"exit={code}",
        )
        if sid == "daily_cache" and not ok:
            self.btn_continue.setEnabled(True)

    def _on_all_done(self) -> None:
        self._set_controls_busy(False)
        self._append_log("本批执行结束。")
        self._refresh_completeness()
        # 终局 Server酱（定时模式）
        if self.scheduled:
            try:
                from run_all_if_trading_day import _notify_daily_batch

                failed = [
                    self.step_by_id[sid].name
                    for sid, st in self.status_by_id.items()
                    if st == STATUS_FAIL and sid in self.step_by_id
                ]
                _notify_daily_batch(
                    self.asof,
                    status="ok" if not failed else "partial_fail",
                    failed=failed,
                    live_only=self.live_only,
                )
            except Exception as e:
                self._append_log(f"终局通知失败: {e}")

    def _ordered_ids(self, wanted: Set[str]) -> List[str]:
        return [s.id for s in self.steps if s.id in wanted and s.id in self.row_by_id]

    def _on_rerun_all(self) -> None:
        ids = self._ordered_ids(set(self.row_by_id.keys()))
        # 手动：日线只检查不长时间等；仍放入列表
        self._start_worker(ids, use_asof_freeze=True, stop_on_gate_fail=True)

    def _on_rerun_selected(self) -> None:
        wanted: Set[str] = set()
        for sid, row in self.row_by_id.items():
            item = self.table.item(row, COL_CHECK)
            if item and item.checkState() == Qt.Checked:
                wanted.add(sid)
        self._start_worker(
            self._ordered_ids(wanted),
            use_asof_freeze=True,
            stop_on_gate_fail=False,
        )

    def _on_continue_after_gate(self) -> None:
        # 从门禁之后继续剩余未成功步骤
        past_gate = False
        wanted: Set[str] = set()
        for s in self.steps:
            if s.id == "daily_cache":
                past_gate = True
                continue
            if not past_gate:
                continue
            if s.id not in self.row_by_id:
                continue
            if self.status_by_id.get(s.id) != STATUS_OK:
                wanted.add(s.id)
        self.btn_continue.setEnabled(False)
        self._start_worker(
            self._ordered_ids(wanted),
            use_asof_freeze=not self.scheduled,
            stop_on_gate_fail=False,
        )

    def _on_retry_failed_row(self) -> None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            # 所有失败项
            wanted = {
                sid for sid, st in self.status_by_id.items() if st == STATUS_FAIL
            }
        else:
            wanted = set()
            for idx in rows:
                for sid, row in self.row_by_id.items():
                    if row == idx.row():
                        wanted.add(sid)
        self._start_worker(
            self._ordered_ids(wanted),
            use_asof_freeze=not self.scheduled,
            stop_on_gate_fail=False,
        )

    def _on_stop_wechat(self) -> None:
        if self.worker:
            self.worker.request_stop_wechat_retry()
            self._append_log("已请求停止公众号自动重试。")

    def _start_scheduled(self) -> None:
        from utils.trading_day import is_tradeday

        if not is_tradeday(self.today):
            for sid in self.row_by_id:
                self._set_status(sid, STATUS_SKIP, "非交易日")
            self._append_log(f"{self.today} 非交易日，跳过批跑。窗口保留至次日 09:00。")
            self.btn_rerun_all.setEnabled(False)
            self.btn_rerun_sel.setEnabled(False)
            return
        self.asof = self.today
        self._update_header()
        self._refresh_completeness()
        ids = [s.id for s in self.steps if s.id in self.row_by_id]
        self._append_log("定时模式：开始自动顺序执行…")
        self._start_worker(ids, use_asof_freeze=False, stop_on_gate_fail=True)

    def _tick_auto_close(self) -> None:
        now = datetime.now()
        remain = self._close_at - now
        if remain.total_seconds() <= 0:
            self._append_log("已到次日 09:00，自动退出。")
            QApplication.instance().quit()
            return
        hrs = int(remain.total_seconds() // 3600)
        mins = int((remain.total_seconds() % 3600) // 60)
        self.statusBar().showMessage(
            f"自动关闭倒计时 {hrs}小时{mins}分（{self._close_at.strftime('%m-%d %H:%M')}）"
            + (" · 执行中…" if self._pipeline_busy else "")
        )


def _parse_args(argv=None):
    p = argparse.ArgumentParser(description="盘后批跑 GUI")
    p.add_argument(
        "--scheduled",
        action="store_true",
        help="定时任务模式：交易日检查 + 自动全流程（由 bat 传入）",
    )
    p.add_argument(
        "--live-only",
        action="store_true",
        help="仅实盘步骤",
    )
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    from utils.live_only import is_live_only_machine

    live_only = bool(args.live_only) or is_live_only_machine()
    app = QApplication(sys.argv)
    win = PostMarketWindow(scheduled=bool(args.scheduled), live_only=live_only)
    win.show()
    # 定时启动时前置到前台
    win.raise_()
    win.activateWindow()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
