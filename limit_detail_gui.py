#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
涨跌停板详情查询 - GUI 程序
输入股票代码和日期，显示该日该股的涨跌停板详情（与 multi_port_web 中涨跌停板详情一致）
支持表格与图形（时间轴）两种展示方式。
"""

import sys
import os
from datetime import date

# 添加项目根目录到路径
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, current_dir)

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QDateEdit, QPushButton, QTableWidget, QTableWidgetItem,
    QMessageBox, QHeaderView, QGroupBox, QTabWidget, QScrollArea,
    QFrame, QSizePolicy
)
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QDate, QRect
from PyQt5.QtGui import QColor, QPainter, QFont


class SegmentBarWidget(QWidget):
    """进度条：封单(蓝)、加单(红)、撤单(黑)、成交量(黄)，与 web 一致"""
    BAR_HEIGHT = 14

    def __init__(self, volume_amount=0, add_amount=0, withdraw_amount=0, final_amount=0, global_max=1, parent=None):
        super().__init__(parent)
        self.volume_amount = volume_amount or 0
        self.add_amount = add_amount or 0
        self.withdraw_amount = withdraw_amount or 0
        self.final_amount = final_amount or 0
        self.global_max = max(global_max, 1)
        self.setFixedHeight(self.BAR_HEIGHT + 4)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)

    def set_values(self, volume_amount, add_amount, withdraw_amount, final_amount, global_max=None):
        self.volume_amount = volume_amount or 0
        self.add_amount = add_amount or 0
        self.withdraw_amount = withdraw_amount or 0
        self.final_amount = final_amount or 0
        if global_max is not None:
            self.global_max = max(global_max, 1)
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        # 加单包含在封单里：封单量=final，其中蓝色=基础封单(final-add)，红色=加单(add)，蓝+红=当前封单
        # 条总长 = 封单 + 撤单 + 成交量（不单独加 add，因已含在封单内）
        total = self.final_amount + self.withdraw_amount + self.volume_amount
        if total <= 0:
            return
        w = self.width()
        h = self.BAR_HEIGHT
        bar_width = int(w * (total / self.global_max))
        if bar_width <= 0:
            return
        x, y = 0, 2
        # 用累计位置画段，避免逐段 int() 产生空隙
        pos0 = 0
        pos1 = round(bar_width * self.final_amount / total)
        pos2 = round(bar_width * (self.final_amount + self.withdraw_amount) / total)
        pos3 = bar_width
        pos1 = max(pos0, min(pos1, pos3))
        pos2 = max(pos1, min(pos2, pos3))

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        # 1. 封单段（蓝+红）[pos0, pos1)，必须填满整段无空隙
        if pos1 > pos0 and self.final_amount > 0:
            seg_w = pos1 - pos0
            base = max(0, self.final_amount - self.add_amount)
            add_part = min(self.add_amount, self.final_amount)
            if base > 0 and self.final_amount > 0:
                blue_w = round(seg_w * base / self.final_amount)
                blue_w = max(0, min(blue_w, seg_w))
                if add_part > 0:
                    red_w = seg_w - blue_w  # 保证蓝+红正好填满 seg_w
                    if blue_w > 0:
                        p.fillRect(x + pos0, y, blue_w, h, QColor('#2196f3'))
                    p.fillRect(x + pos0 + blue_w, y, red_w, h, QColor('#f44336'))
                else:
                    p.fillRect(x + pos0, y, seg_w, h, QColor('#2196f3'))
            else:
                if add_part > 0:
                    p.fillRect(x + pos0, y, seg_w, h, QColor('#f44336'))
                else:
                    p.fillRect(x + pos0, y, seg_w, h, QColor('#2196f3'))
        # 2. 撤单段（黑）[pos1, pos2)，与封单重叠 1 像素避免抗锯齿/缩放产生缝隙
        if pos2 > pos1 and self.withdraw_amount > 0:
            start = max(pos0, pos1 - 1)
            p.fillRect(x + start, y, pos2 - start, h, QColor('#000000'))
        # 3. 成交量段（黄）[pos2, pos3)
        if pos3 > pos2 and self.volume_amount > 0:
            p.fillRect(x + pos2, y, pos3 - pos2, h, QColor('#ffc107'))
        p.end()


def _draw_bar_segments(p, x, y, w, h, volume_amount, add_amount, withdraw_amount, final_amount, global_max):
    """在给定矩形内绘制一段进度条（封单蓝+红、撤单黑、成交量黄），加单含在封单内。"""
    total = final_amount + withdraw_amount + volume_amount
    if total <= 0 or global_max <= 0:
        return
    bar_width = int(w * (total / global_max))
    if bar_width <= 0:
        return
    pos0, pos1 = 0, round(bar_width * final_amount / total)
    pos2 = round(bar_width * (final_amount + withdraw_amount) / total)
    pos3 = bar_width
    pos1 = max(pos0, min(pos1, pos3))
    pos2 = max(pos1, min(pos2, pos3))
    # 封单段 [pos0, pos1)
    if pos1 > pos0 and final_amount > 0:
        seg_w = pos1 - pos0
        base = max(0, final_amount - add_amount)
        add_part = min(add_amount, final_amount)
        if base > 0 and final_amount > 0:
            blue_w = max(0, min(round(seg_w * base / final_amount), seg_w))
            red_w = seg_w - blue_w
            if blue_w > 0:
                p.fillRect(x + pos0, y, blue_w, h, QColor('#2196f3'))
            if red_w > 0:
                p.fillRect(x + pos0 + blue_w, y, red_w, h, QColor('#f44336'))
        else:
            p.fillRect(x + pos0, y, seg_w, h, QColor('#f44336') if add_part > 0 else QColor('#2196f3'))
    if pos2 > pos1 and withdraw_amount > 0:
        start = max(pos0, pos1 - 1)
        p.fillRect(x + start, y, pos2 - start, h, QColor('#000000'))
    if pos3 > pos2 and volume_amount > 0:
        p.fillRect(x + pos2, y, pos3 - pos2, h, QColor('#ffc107'))


class LimitTimelineCanvas(QWidget):
    """单一画布直接绘制所有进度条，行距紧凑，无需列表控件"""
    ROW_HEIGHT = 14
    TITLE_HEIGHT = 22
    BAR_WIDTH = 280
    TIME_W = 68
    DOT_W = 10
    STATUS_W = 92

    def __init__(self, parent=None):
        super().__init__(parent)
        self.details = []
        self.global_max_total = 1
        self.setMinimumSize(520, 120)
        self.setStyleSheet("background: #fafafa;")

    def set_details(self, details: list):
        self.details = details or []
        self.global_max_total = 0
        for d in self.details:
            v = d.get('volume_amount', 0) or d.get('volume', 0)
            wd = d.get('withdraw_amount', 0)
            f = d.get('final_amount', 0) or d.get('bid_vol', 0) or d.get('ask_vol', 0)
            t = f + wd + v
            self.global_max_total = max(self.global_max_total, t)
        if self.global_max_total <= 0:
            self.global_max_total = 1
        h = self.TITLE_HEIGHT + len(self.details) * self.ROW_HEIGHT
        self.setMinimumHeight(max(80, h))
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        w = self.width()
        if not self.details:
            p.drawText(QRect(0, self.TITLE_HEIGHT, w, 24), Qt.AlignCenter, "暂无涨跌停板数据")
            p.end()
            return
        # 标题
        p.setFont(QFont("", 11, QFont.Bold))
        p.setPen(QColor("#333"))
        p.drawText(8, 16, "涨跌停板时间轴")
        # 每行
        bar_h = max(2, self.ROW_HEIGHT - 4)
        x_time, x_dot, x_status, x_bar = 8, 8 + self.TIME_W, 8 + self.TIME_W + self.DOT_W, 8 + self.TIME_W + self.DOT_W + self.STATUS_W
        for i, d in enumerate(self.details):
            y_row = self.TITLE_HEIGHT + i * self.ROW_HEIGHT
            y_bar = y_row + (self.ROW_HEIGHT - bar_h) // 2
            status = d.get('status') or d.get('node_type') or ''
            is_up = '涨停' in status
            is_down = '跌停' in status
            dot_color = QColor('#ff5722' if is_up else '#4caf50' if is_down else '#2196f3')
            status_color = QColor('#ff5722' if is_up else '#4caf50' if is_down else '#2196f3')
            # 时间
            p.setPen(QColor("#666"))
            p.setFont(QFont("", 9, QFont.Normal))
            p.drawText(QRect(8, y_row, self.TIME_W - 4, self.ROW_HEIGHT), Qt.AlignRight | Qt.AlignVCenter, d.get('time', ''))
            # 圆点
            p.fillRect(x_dot, y_row + (self.ROW_HEIGHT - 6) // 2, 6, 6, dot_color)
            # 状态+价
            status_text = status
            price = d.get('price', 0)
            if price and status in ('涨停', '跌停'):
                status_text = f"{status} {price:.2f}"
            p.setPen(status_color)
            p.setFont(QFont("", 9, QFont.Bold))
            p.drawText(QRect(x_status, y_row, self.STATUS_W - 4, self.ROW_HEIGHT), Qt.AlignLeft | Qt.AlignVCenter, status_text[:10])
            # 进度条
            v = d.get('volume_amount', 0) or d.get('volume', 0)
            a = d.get('add_amount', 0)
            wd = d.get('withdraw_amount', 0)
            f = d.get('final_amount', 0) or d.get('bid_vol', 0) or d.get('ask_vol', 0)
            _draw_bar_segments(p, x_bar, y_bar, self.BAR_WIDTH, bar_h, v, a, wd, f, self.global_max_total)
            # 文字在条后
            tip = "买一" if is_up else "卖一"
            tip_text = f"{tip}:{f}"
            if a > 0:
                tip_text += f" 加:{a}"
            if wd > 0:
                tip_text += f" 撤:{wd}"
            tip_text += f" 量:{v}"
            p.setPen(QColor("#333"))
            p.setFont(QFont("", 8))
            p.drawText(QRect(x_bar + self.BAR_WIDTH + 6, y_row, w - (x_bar + self.BAR_WIDTH + 10), self.ROW_HEIGHT), Qt.AlignLeft | Qt.AlignVCenter, tip_text)
        p.end()


class LimitTimelineView(QWidget):
    """涨跌停板时间轴图形视图（内嵌画布+滚动）"""
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.canvas = LimitTimelineCanvas()
        self.scroll.setWidget(self.canvas)
        layout.addWidget(self.scroll)

    def set_details(self, details: list):
        self.canvas.set_details(details)


class AnalyzeThread(QThread):
    """分析线程，避免阻塞界面"""
    finished = pyqtSignal(dict)   # 成功：analysis_result
    error = pyqtSignal(str)      # 失败：错误信息

    def __init__(self, stock_code: str, analysis_date: date):
        super().__init__()
        self.stock_code = stock_code.strip()
        self.analysis_date = analysis_date

    def run(self):
        try:
            from core.stock_analyzer import StockAnalyzer
            analyzer = StockAnalyzer()
            result = analyzer.analyze_stock(self.stock_code, self.analysis_date)
            if result.get('error'):
                self.error.emit(result['error'])
                return
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))


class LimitDetailWindow(QMainWindow):
    """涨跌停板详情主窗口"""

    def __init__(self):
        super().__init__()
        self.analyze_thread = None
        self.setWindowTitle("涨跌停板详情查询")
        self.setMinimumSize(720, 480)
        self.resize(900, 560)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)

        # 输入区
        input_group = QGroupBox("查询条件")
        input_layout = QHBoxLayout(input_group)
        input_layout.addWidget(QLabel("股票代码:"))
        self.stock_edit = QLineEdit()
        self.stock_edit.setPlaceholderText("例如 600519")
        self.stock_edit.setMaximumWidth(120)
        input_layout.addWidget(self.stock_edit)
        input_layout.addWidget(QLabel("日期:"))
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        input_layout.addWidget(self.date_edit)
        self.query_btn = QPushButton("查询")
        self.query_btn.clicked.connect(self.on_query)
        input_layout.addWidget(self.query_btn)
        input_layout.addStretch()
        layout.addWidget(input_group)

        # 标题区（显示股票名、日期等）
        self.title_label = QLabel("请输入股票代码和日期后点击「查询」")
        self.title_label.setStyleSheet("font-weight: bold; color: #333;")
        layout.addWidget(self.title_label)

        # 表格 + 图形 双 Tab
        self.tabs = QTabWidget()
        # 表格
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels([
            "时间", "状态", "价格", "成交量", "封单量(买一/卖一)", "加单数量", "撤单数量"
        ])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setAlternatingRowColors(True)
        self.tabs.addTab(self.table, "表格")
        # 图形（时间轴，与 web 一致）
        self.timeline_view = LimitTimelineView()
        self.tabs.addTab(self.timeline_view, "图形")
        layout.addWidget(self.tabs)

    def on_query(self):
        stock_code = self.stock_edit.text().strip()
        if not stock_code:
            QMessageBox.warning(self, "提示", "请输入股票代码")
            return
        analysis_date = self.date_edit.date().toPyDate()
        self.query_btn.setEnabled(False)
        self.title_label.setText(f"正在查询 {stock_code} {analysis_date} ...")
        self.table.setRowCount(0)

        self.analyze_thread = AnalyzeThread(stock_code, analysis_date)
        self.analyze_thread.finished.connect(self.on_analysis_finished)
        self.analyze_thread.error.connect(self.on_analysis_error)
        self.analyze_thread.start()

    def on_analysis_finished(self, result: dict):
        self.query_btn.setEnabled(True)
        stock_code = result.get('stock_code', '')
        analysis_date = result.get('analysis_date', '')
        try:
            stock_name = self._get_stock_name(stock_code)
        except Exception:
            stock_name = ''
        self.title_label.setText(f"{stock_code} {stock_name} {analysis_date} 涨跌停板详情（每个 tick）")

        limit_up_analysis = result.get('limit_up_analysis') or {}
        # 使用涨跌停期间每一个 tick 的明细（limit_all_ticks），不再只用关键节点
        limit_details = limit_up_analysis.get('limit_all_ticks') or limit_up_analysis.get('limit_details') or []

        if not limit_details:
            self.table.setRowCount(1)
            self.table.setItem(0, 0, QTableWidgetItem("暂无涨跌停板数据"))
            for j in range(1, 7):
                self.table.setItem(0, j, QTableWidgetItem(""))
            self.timeline_view.set_details([])
            return

        self.timeline_view.set_details(limit_details)
        self.table.setRowCount(len(limit_details))
        for i, detail in enumerate(limit_details):
            status = detail.get('status') or detail.get('node_type') or ''
            if '涨停' in status and '开板' not in status:
                row_bg = QColor('#ffcccc')
            elif '跌停' in status and '开板' not in status:
                row_bg = QColor('#ccffcc')
            else:
                row_bg = QColor('#ccccff')

            def cell(text):
                item = QTableWidgetItem(str(text))
                item.setBackground(row_bg)
                return item

            self.table.setItem(i, 0, cell(detail.get('time', '')))
            self.table.setItem(i, 1, cell(status))
            self.table.setItem(i, 2, cell(f"{detail.get('price', 0):.2f}"))

            # 成交量（与 web 中 volume_amount 一致）
            vol = detail.get('volume_amount', 0) or detail.get('volume', 0)
            self.table.setItem(i, 3, cell(vol))

            # 封单量：涨停=买一量，跌停=卖一量（与 web 中 final_amount / bid_vol / ask_vol 一致）
            final_amt = detail.get('final_amount', 0)
            if final_amt == 0:
                if detail.get('is_limit_up'):
                    final_amt = detail.get('bid_vol', 0)
                elif detail.get('is_limit_down'):
                    final_amt = detail.get('ask_vol', 0)
                else:
                    if '涨停' in status and '开板' not in status:
                        final_amt = detail.get('bid_vol', 0)
                    elif '跌停' in status and '开板' not in status:
                        final_amt = detail.get('ask_vol', 0)
                    else:
                        final_amt = detail.get('bid_vol', 0) or detail.get('ask_vol', 0)
            self.table.setItem(i, 4, cell(final_amt))

            # 加单数量、撤单数量（与 web 中 add_amount、withdraw_amount 一致）
            self.table.setItem(i, 5, cell(detail.get('add_amount', 0)))
            self.table.setItem(i, 6, cell(detail.get('withdraw_amount', 0)))

    def on_analysis_error(self, error_msg: str):
        self.query_btn.setEnabled(True)
        self.title_label.setText("查询失败")
        QMessageBox.critical(self, "错误", f"分析失败：\n{error_msg}")
        self.table.setRowCount(0)
        self.timeline_view.set_details([])

    def _get_stock_name(self, stock_code: str) -> str:
        try:
            from key_price_calculator import KeyPriceCalculator
            calc = KeyPriceCalculator()
            return calc.get_stock_name(stock_code) or ''
        except Exception:
            return ''


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    win = LimitDetailWindow()
    win.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
