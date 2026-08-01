#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
执行记录查看对话框
用于查看每天的任务执行记录
"""

import csv
import json
import os

from PyQt5.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QTableWidget,
                             QTableWidgetItem, QPushButton, QLabel, QDateEdit,
                             QGroupBox, QMessageBox, QFileDialog)
from PyQt5.QtCore import Qt, QDate
from datetime import date, timedelta
from core.execution_record_manager import ExecutionRecordManager


TABLE_COLUMNS = [
    "执行时间", "股票代码", "股票名称", "规则类型", "规则名称",
    "规则详情", "当前价", "交易价格", "交易数量", "订单号",
    "需要审核", "审核结果", "审核时间",
]

APPROVAL_RESULT_CN = {
    'approved': '已批准',
    'rejected': '已拒绝',
    'cancelled': '已取消',
    'auto': '已下单',
    'skipped': '已跳过',
    'not_true_breakthrough': '非真突破',
    'band_hard_pass': '价格带放弃',
    'buy_block_window': '禁买窗口',
    'no_cash': '余额不足',
    'min_buy_amount': '低于最小买入',
    'order_below_min': '本笔低于最小买入',
    'early_cancelled': '提前撤单',
    'no_position': '无持仓',
    'order_failed': '下单失败',
}

ORDER_ID_LABELS = {
    'CANCELLED': '已取消',
    'NOT_TRUE_BREAKTHROUGH': '非真突破',
    'BAND_HARD_PASS': '价格带放弃',
    'SKIPPED_BUY_WINDOW': '禁买跳过',
    'NO_CASH': '余额不足',
    'MIN_BUY_AMOUNT': '低于最小买入',
    'SKIPPED_MIN_BUY': '本笔低于最小买入',
    'EARLY_CANCELLED': '提前撤单',
    'NO_POSITION': '无持仓',
    'ORDER_FAILED': '下单失败',
}

_SKIPPED_APPROVAL_RESULTS = frozenset({
    'skipped',
    'not_true_breakthrough',
    'band_hard_pass',
    'buy_block_window',
    'no_cash',
    'min_buy_amount',
    'order_below_min',
    'no_position',
})


class ExecutionRecordsDialog(QDialog):
    """执行记录查看对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("执行记录查看")
        self.setMinimumSize(1200, 600)
        
        self.execution_record_manager = ExecutionRecordManager()
        
        self.setup_ui()
        self.load_today_records()
    
    def setup_ui(self):
        """设置UI"""
        layout = QVBoxLayout(self)
        
        # 日期选择区域
        date_group = QGroupBox("日期选择")
        date_layout = QHBoxLayout()
        
        date_layout.addWidget(QLabel("查看日期："))
        
        self.date_edit = QDateEdit()
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("yyyy-MM-dd")
        self.date_edit.dateChanged.connect(self.on_date_changed)
        date_layout.addWidget(self.date_edit)
        
        today_btn = QPushButton("今天")
        today_btn.clicked.connect(self.load_today_records)
        date_layout.addWidget(today_btn)
        
        prev_btn = QPushButton("前一天")
        prev_btn.clicked.connect(self.load_prev_day)
        date_layout.addWidget(prev_btn)
        
        next_btn = QPushButton("后一天")
        next_btn.clicked.connect(self.load_next_day)
        date_layout.addWidget(next_btn)
        
        date_layout.addStretch()
        
        # 统计信息
        self.stats_label = QLabel("")
        self.stats_label.setStyleSheet("font-weight: bold; color: #2196F3;")
        date_layout.addWidget(self.stats_label)
        
        date_group.setLayout(date_layout)
        layout.addWidget(date_group)

        export_group = QGroupBox("导出记录")
        export_layout = QHBoxLayout()
        export_layout.addWidget(QLabel("开始日期："))
        self.export_start_date = QDateEdit()
        self.export_start_date.setCalendarPopup(True)
        self.export_start_date.setDisplayFormat("yyyy-MM-dd")
        self.export_start_date.setDate(QDate.currentDate())
        export_layout.addWidget(self.export_start_date)
        export_layout.addWidget(QLabel("结束日期："))
        self.export_end_date = QDateEdit()
        self.export_end_date.setCalendarPopup(True)
        self.export_end_date.setDisplayFormat("yyyy-MM-dd")
        self.export_end_date.setDate(QDate.currentDate())
        export_layout.addWidget(self.export_end_date)
        sync_range_btn = QPushButton("同步查看日期")
        sync_range_btn.setToolTip("将导出范围设为当前「查看日期」当天")
        sync_range_btn.clicked.connect(self._sync_export_range_to_view_date)
        export_layout.addWidget(sync_range_btn)
        export_layout.addStretch()
        export_btn = QPushButton("导出…")
        export_btn.clicked.connect(self.export_records)
        export_layout.addWidget(export_btn)
        export_group.setLayout(export_layout)
        layout.addWidget(export_group)
        
        # 表格
        self.table = QTableWidget()
        self.setup_table()
        layout.addWidget(self.table)
        
        # 底部按钮
        bottom_layout = QHBoxLayout()
        bottom_layout.addStretch()
        
        refresh_btn = QPushButton("刷新")
        refresh_btn.clicked.connect(self.refresh_records)
        bottom_layout.addWidget(refresh_btn)
        
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        bottom_layout.addWidget(close_btn)
        
        layout.addLayout(bottom_layout)
    
    def setup_table(self):
        """设置表格"""
        self.table.setColumnCount(len(TABLE_COLUMNS))
        self.table.setHorizontalHeaderLabels(TABLE_COLUMNS)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        
        # 设置列宽
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, header.ResizeToContents)  # 执行时间
        header.setSectionResizeMode(1, header.ResizeToContents)  # 股票代码
        header.setSectionResizeMode(2, header.ResizeToContents)  # 股票名称
        header.setSectionResizeMode(3, header.ResizeToContents)  # 规则类型
        header.setSectionResizeMode(4, header.ResizeToContents)  # 规则名称
        header.setSectionResizeMode(5, header.Stretch)  # 规则详情（可拉伸）
        header.setSectionResizeMode(6, header.ResizeToContents)  # 当前价
        header.setSectionResizeMode(7, header.ResizeToContents)  # 交易价格
        header.setSectionResizeMode(8, header.ResizeToContents)  # 交易数量
        header.setSectionResizeMode(9, header.ResizeToContents)  # 订单号
        header.setSectionResizeMode(10, header.ResizeToContents)  # 需要审核
        header.setSectionResizeMode(11, header.ResizeToContents)  # 审核结果
        header.setSectionResizeMode(12, header.ResizeToContents)  # 审核时间
    
    def load_today_records(self):
        """加载今天的记录"""
        self.date_edit.setDate(QDate.currentDate())
        self.refresh_records()
    
    def load_prev_day(self):
        """加载前一天的记录"""
        current_date = self.date_edit.date().toPyDate()
        prev_date = current_date - timedelta(days=1)
        self.date_edit.setDate(QDate.fromString(prev_date.strftime("%Y-%m-%d"), "yyyy-MM-dd"))
        self.refresh_records()
    
    def load_next_day(self):
        """加载后一天的记录"""
        current_date = self.date_edit.date().toPyDate()
        next_date = current_date + timedelta(days=1)
        today = date.today()
        if next_date > today:
            QMessageBox.information(self, "提示", "无法查看未来的记录")
            return
        self.date_edit.setDate(QDate.fromString(next_date.strftime("%Y-%m-%d"), "yyyy-MM-dd"))
        self.refresh_records()
    
    def on_date_changed(self):
        """日期改变时的处理"""
        self.refresh_records()

    def _sync_export_range_to_view_date(self):
        """将导出起止日期设为当前查看日期。"""
        view_date = self.date_edit.date()
        self.export_start_date.setDate(view_date)
        self.export_end_date.setDate(view_date)

    @staticmethod
    def _format_order_id_text(order_id) -> str:
        if order_id in ORDER_ID_LABELS:
            return ORDER_ID_LABELS[order_id]
        if order_id == 'CANCELLED':
            return '已取消'
        return str(order_id or '')

    @staticmethod
    def _display_hard_pass_as_abandon(text) -> str:
        """执行记录展示：硬pass → 放弃（兼容历史记录）。"""
        s = str(text or "")
        if "硬pass" in s:
            return s.replace("硬pass", "放弃")
        return s

    @classmethod
    def _record_to_display_row(cls, record: dict) -> list:
        """将单条记录格式化为表格/导出用的一行文本。"""
        current_price = record.get('current_price', 0)
        trade_price = record.get('trade_price', 0)
        trade_volume = record.get('trade_volume', 0)
        require_manual_approval = record.get('require_manual_approval', False)
        approval_result = record.get('approval_result', '')
        approval_time = record.get('approval_time', '')
        outcome = str(record.get('execution_outcome') or '')
        approval_result_cn = APPROVAL_RESULT_CN.get(approval_result, approval_result)
        approval_result_cn = cls._display_hard_pass_as_abandon(approval_result_cn)
        if require_manual_approval:
            approval_display = approval_result_cn
            approval_time_display = approval_time or "—"
        elif outcome == 'order_failed' or approval_result == 'order_failed':
            approval_display = APPROVAL_RESULT_CN.get('order_failed', '下单失败')
            approval_time_display = "—"
        elif outcome == 'skipped' or approval_result in _SKIPPED_APPROVAL_RESULTS:
            approval_display = approval_result_cn or '未下单'
            approval_time_display = "—"
        else:
            approval_display = "—"
            approval_time_display = "—"

        return [
            record.get('execution_time', ''),
            record.get('stock_code', ''),
            record.get('stock_name', ''),
            record.get('rule_type_cn', record.get('rule_type', '')),
            cls._display_hard_pass_as_abandon(record.get('rule_name', '')),
            cls._display_hard_pass_as_abandon(record.get('rule_detail', '')),
            f"{current_price:.2f}" if current_price else "—",
            f"{trade_price:.2f}" if trade_price else "—",
            f"{trade_volume}" if trade_volume else "—",
            cls._display_hard_pass_as_abandon(cls._format_order_id_text(record.get('order_id', ''))),
            "是" if require_manual_approval else "否",
            approval_display,
            approval_time_display,
        ]

    def export_records(self):
        """导出日期范围内的全部执行记录。"""
        start_date = self.export_start_date.date().toPyDate()
        end_date = self.export_end_date.date().toPyDate()
        if start_date > end_date:
            QMessageBox.warning(self, "提示", "开始日期不能晚于结束日期。")
            return
        if end_date > date.today():
            QMessageBox.warning(self, "提示", "结束日期不能晚于今天。")
            return

        records = self.execution_record_manager.get_records_by_date_range(
            start_date, end_date
        )
        if not records:
            QMessageBox.information(
                self,
                "提示",
                f"{start_date.strftime('%Y-%m-%d')} 至 {end_date.strftime('%Y-%m-%d')} 没有可导出的执行记录。",
            )
            return

        default_name = (
            f"execution_records_{start_date.strftime('%Y%m%d')}_"
            f"{end_date.strftime('%Y%m%d')}.csv"
        )
        file_path, selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出执行记录",
            default_name,
            "CSV 文件 (*.csv);;JSON 文件 (*.json);;所有文件 (*.*)",
        )
        if not file_path:
            return

        try:
            ext = os.path.splitext(file_path)[1].lower()
            if not ext:
                if "JSON" in (selected_filter or ""):
                    file_path += ".json"
                else:
                    file_path += ".csv"
                ext = os.path.splitext(file_path)[1].lower()

            if ext == ".json":
                self._export_records_json(file_path, records, start_date, end_date)
            else:
                self._export_records_csv(file_path, records)

            QMessageBox.information(
                self,
                "导出完成",
                f"已导出 {len(records)} 条记录到：\n{file_path}",
            )
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出执行记录失败：{str(e)}")

    def _export_records_csv(self, file_path: str, records: list):
        rows = [self._record_to_display_row(r) for r in records]
        with open(file_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(TABLE_COLUMNS)
            writer.writerows(rows)

    def _export_records_json(
        self,
        file_path: str,
        records: list,
        start_date: date,
        end_date: date,
    ):
        payload = {
            "start_date": start_date.strftime("%Y-%m-%d"),
            "end_date": end_date.strftime("%Y-%m-%d"),
            "count": len(records),
            "records": records,
        }
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    
    def refresh_records(self):
        """刷新记录"""
        try:
            # 获取选中的日期
            selected_date = self.date_edit.date().toPyDate()
            
            # 获取记录
            records = self.execution_record_manager.get_records_by_date(selected_date)
            
            # 清空表格
            self.table.setRowCount(0)
            
            # 填充数据
            for record in records:
                row = self.table.rowCount()
                self.table.insertRow(row)
                
                approval_result = record.get('approval_result', '')
                items = self._record_to_display_row(record)

                for col, value in enumerate(items):
                    item = QTableWidgetItem(str(value))
                    item.setTextAlignment(Qt.AlignCenter | Qt.AlignVCenter)
                    
                    # 根据审核/执行结果设置颜色
                    if col == 11:  # 审核结果列
                        if approval_result == 'approved':
                            item.setForeground(Qt.darkGreen)
                        elif approval_result == 'rejected':
                            item.setForeground(Qt.darkRed)
                        elif approval_result == 'cancelled':
                            item.setForeground(Qt.darkGray)
                        elif approval_result in (
                            'skipped',
                            'not_true_breakthrough',
                            'band_hard_pass',
                            'buy_block_window',
                            'no_cash',
                            'min_buy_amount',
                            'no_position',
                            'order_below_min',
                        ) or record.get('execution_outcome') == 'skipped':
                            item.setForeground(Qt.darkYellow)
                        elif approval_result == 'order_failed' or record.get('execution_outcome') == 'order_failed':
                            item.setForeground(Qt.darkMagenta)
                    
                    self.table.setItem(row, col, item)
            
            # 更新统计信息
            approved_count = sum(1 for r in records if r.get('approval_result') == 'approved')
            rejected_count = sum(1 for r in records if r.get('approval_result') == 'rejected')
            cancelled_count = sum(1 for r in records if r.get('approval_result') == 'cancelled')
            auto_count = sum(1 for r in records if r.get('approval_result') == 'auto')
            skipped_count = sum(
                1
                for r in records
                if r.get('execution_outcome') == 'skipped'
                or r.get('approval_result') in (
                    'skipped',
                    'not_true_breakthrough',
                    'band_hard_pass',
                    'buy_block_window',
                    'no_cash',
                    'min_buy_amount',
                    'no_position',
                    'order_below_min',
                )
            )
            order_failed_count = sum(
                1
                for r in records
                if r.get('execution_outcome') == 'order_failed'
                or r.get('approval_result') == 'order_failed'
            )
            total_count = len(records)
            
            date_str = selected_date.strftime('%Y-%m-%d')
            if selected_date == date.today():
                date_str = "今天"
            
            stats_text = f"{date_str} 共 {total_count} 条记录"
            if total_count > 0:
                if any(r.get('require_manual_approval', False) for r in records):
                    stats_text += f" | 已批准: {approved_count} | 已拒绝: {rejected_count} | 已取消: {cancelled_count}"
                if auto_count > 0:
                    stats_text += f" | 已下单: {auto_count}"
                if skipped_count > 0:
                    stats_text += f" | 未下单: {skipped_count}"
                if order_failed_count > 0:
                    stats_text += f" | 下单失败: {order_failed_count}"
            
            self.stats_label.setText(stats_text)
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"加载执行记录失败：{str(e)}")

