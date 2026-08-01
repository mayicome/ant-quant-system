# -*- coding: utf-8 -*-
"""龙虎榜溢价预测复盘小程序。

对照上一交易日龙虎榜「次日溢价预测」与验证日真实开高低收。

  python lhb_premium_verify_gui.py
  python lhb_premium_verify_gui.py --auto-run
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime

from PyQt5.QtCore import QDate, QTimer, Qt
from PyQt5.QtWidgets import (
    QApplication,
    QDateEdit,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if os.path.join(ROOT, "tools") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "tools"))

from lhb_premium_daily_verify import (  # noqa: E402
    find_lhb_file,
    prev_tradeday_before,
    run_verify,
    ymd,
)


class LhbPremiumVerifyDialog(QDialog):
    def __init__(self, parent=None, auto_run: bool = False):
        super().__init__(parent)
        self._auto_run = bool(auto_run)
        self.setWindowTitle("蚂蚁量化 - 龙虎榜溢价预测复盘")
        self.resize(820, 640)

        layout = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("验证日："))
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.setDate(QDate.currentDate())
        row.addWidget(self.date_edit)
        self.run_btn = QPushButton("开始复盘")
        self.run_btn.clicked.connect(self._run)
        row.addWidget(self.run_btn)
        row.addStretch(1)
        layout.addLayout(row)

        self.status = QLabel("选择验证日：将用「上一交易日」的龙虎榜预测，对照验证日行情。")
        layout.addWidget(self.status)
        self.result = QTextEdit()
        self.result.setReadOnly(True)
        layout.addWidget(self.result)

        if self._auto_run:
            QTimer.singleShot(200, self._run)

    def _run(self):
        qd = self.date_edit.date()
        verify_d = date(qd.year(), qd.month(), qd.day())
        verify_ymd = ymd(verify_d)
        pred_d = prev_tradeday_before(verify_d)
        if pred_d is None:
            QMessageBox.warning(self, "失败", f"找不到 {verify_ymd} 的上一交易日")
            if self._auto_run:
                QTimer.singleShot(3000, self.accept)
            return
        pred_ymd = ymd(pred_d)
        if find_lhb_file(pred_ymd) is None:
            msg = f"未找到龙虎榜解析_{pred_ymd}.xlsx（含存档）"
            self.status.setText(msg)
            self.result.setPlainText(msg)
            if self._auto_run:
                QTimer.singleShot(5000, self.accept)
            return

        self.run_btn.setEnabled(False)
        self.status.setText(f"复盘中：预测日 {pred_ymd} → 验证日 {verify_ymd} …")
        QApplication.processEvents()
        try:
            rc, headline, summary, detail = run_verify(
                verify_ymd=verify_ymd,
                pred_ymd=pred_ymd,
                write_png=True,
            )
        except Exception as e:
            self.status.setText(f"失败: {e}")
            self.result.setPlainText(str(e))
            self.run_btn.setEnabled(True)
            if self._auto_run:
                QTimer.singleShot(8000, self.accept)
            return

        lines = [
            f"预测日 {pred_ymd} → 验证日 {verify_ymd}",
            "",
            "【一句话】",
            headline,
            "",
            "【分档对照】",
            summary.to_string(index=False) if summary is not None and not summary.empty else "(空)",
            "",
            f"有效 {(detail['有效']==True).sum() if detail is not None and not detail.empty else 0} / "
            f"{len(detail) if detail is not None else 0}",
            "",
            f"Excel: history_data/龙虎榜溢价验证日报_{verify_ymd}.xlsx",
            f"PNG:   history_data/龙虎榜溢价验证日报_{verify_ymd}.png",
        ]
        self.result.setPlainText("\n".join(lines))
        self.status.setText("完成" if rc == 0 else f"完成（退出码 {rc}）")
        self.run_btn.setEnabled(True)
        if self._auto_run:
            QTimer.singleShot(8000, self.accept)


def main() -> int:
    parser = argparse.ArgumentParser(description="龙虎榜溢价预测复盘")
    parser.add_argument(
        "--auto-run",
        action="store_true",
        help="启动后自动复盘（验证日=今天），写出 Excel/PNG，约 8 秒后退出",
    )
    args, _unknown = parser.parse_known_args()

    # 纯批跑可走 CLI，避免无显示环境时 GUI 问题；有 auto-run 仍开对话框便于日志
    if args.auto_run and os.environ.get("LHB_PREMIUM_VERIFY_HEADLESS") == "1":
        from lhb_premium_daily_verify import main as cli_main

        sys.argv = [sys.argv[0], "--auto-run"]
        return int(cli_main() or 0)

    app = QApplication(sys.argv)
    dlg = LhbPremiumVerifyDialog(auto_run=bool(args.auto_run))
    dlg.show()
    return int(app.exec_())


if __name__ == "__main__":
    raise SystemExit(main())
