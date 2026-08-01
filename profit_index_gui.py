#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
独立 GUI 版：只计算“赚钱指数”（不包含任何涨停筛选逻辑）
"""

import sys
from typing import Optional

import json
import os
import inspect
import warnings
import argparse
from datetime import datetime

from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import (
    QCheckBox,
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

# 复用 auto_limit_up_filter.py 里已实现的计算线程和图表组件
from profit_index_components import ProfitIndexCalculatorThread, ProfitIndexChartWidget

# 尽量减少 Qt/Matplotlib 的控制台噪音
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings(
    "ignore",
    message="This figure includes Axes that are not compatible with tight_layout*",
    category=UserWarning,
)

ALL_STOCK_INFO_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "all_a_stock_info.json")


def print_concept_stock_summary(path: str = ALL_STOCK_INFO_PATH, min_n: int = 15, max_n: int = 120):
    """
    从 all_a_stock_info.json 反算：概念 -> 个股集合
    并打印个股数在 [min_n, max_n] 的概念明细。
    """
    if not os.path.exists(path):
        print(f"概念反算失败：文件不存在 {path}")
        return

    try:
        with open(path, "r", encoding="utf-8") as f:
            all_info = json.load(f) or {}
    except Exception as e:
        print(f"概念反算失败：读取 JSON 出错: {e}")
        return

    if not isinstance(all_info, dict):
        print("概念反算失败：all_a_stock_info.json 顶层结构不是对象")
        return

    concept_to_stocks = {}
    for raw_code, info in all_info.items():
        if not isinstance(info, dict):
            continue
        code = str(info.get("stock_code") or raw_code or "").strip()
        name = str(info.get("name") or "").strip()
        concepts = info.get("concepts") or []
        if not code or not isinstance(concepts, list):
            continue

        for c in concepts:
            concept = str(c or "").strip()
            if not concept:
                continue
            if concept not in concept_to_stocks:
                concept_to_stocks[concept] = {}
            concept_to_stocks[concept][code] = name

    rows = []
    for concept, stocks in concept_to_stocks.items():
        cnt = len(stocks)
        if cnt < int(min_n) or cnt > int(max_n):
            continue
        sample_items = list(stocks.items())[:10]
        sample_text = ", ".join([f"{k}({v})" if v else k for k, v in sample_items])
        rows.append((concept, cnt, sample_text))

    rows.sort(key=lambda x: (-x[1], x[0]))

    print(f"========== 概念反算结果（{min_n}~{max_n}只）==========")
    print(f"数据来源: {path}")
    print(f"概念数量: {len(rows)}")
    if not rows:
        print("无符合条件的概念")
        return
    for concept, cnt, sample in rows:
        print(f"{concept} | 个股数:{cnt} | 样例:{sample}")
    print("========== 概念反算结束 ==========\n")


class ProfitIndexDialog(QDialog):
    def __init__(self, days: int = 30, parent=None, auto_run: bool = False):
        super().__init__(parent)
        self.setWindowTitle("蚂蚁量化 - 赚钱指数（独立版）")
        self.resize(1100, 750)

        self._auto_run = bool(auto_run)
        self.profit_index_thread: Optional[ProfitIndexCalculatorThread] = None
        # 由于我们已将组件抽离到 `profit_index_components.py`，这里保留兼容逻辑是为了防止你本地版本未完全同步。
        self._thread_supports_stock_limit = False
        try:
            sig = inspect.signature(ProfitIndexCalculatorThread.__init__)
            self._thread_supports_stock_limit = "stock_limit" in sig.parameters
        except Exception:
            self._thread_supports_stock_limit = False

        self._settings_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(self._settings_dir, exist_ok=True)
        self._settings_path = os.path.join(self._settings_dir, "profit_index_settings.json")

        layout = QVBoxLayout(self)

        # 参数区
        param_row = QHBoxLayout()
        layout.addLayout(param_row)

        param_row.addWidget(QLabel("显示最近交易日数："))
        self.days_spin = QSpinBox()
        self.days_spin.setRange(5, 120)
        self.days_spin.setValue(days)
        param_row.addWidget(self.days_spin)

        self.test_stock_cb = QCheckBox("测试：只取前N只")
        self.test_stock_cb.setChecked(True)
        param_row.addWidget(self.test_stock_cb)

        self.test_stock_spin = QSpinBox()
        self.test_stock_spin.setRange(1, 50000)
        self.test_stock_spin.setValue(100)
        self.test_stock_spin.setEnabled(True)
        param_row.addWidget(self.test_stock_spin)

        # 如果线程不支持 stock_limit，则禁用测试模式，避免传参报错
        if not self._thread_supports_stock_limit:
            self.test_stock_cb.setChecked(False)
            self.test_stock_cb.setEnabled(False)
            self.test_stock_spin.setEnabled(False)
        else:
            self.test_stock_cb.toggled.connect(self.test_stock_spin.setEnabled)

        self.start_btn = QPushButton("开始计算")
        param_row.addWidget(self.start_btn)

        # 图表区
        self.chart = ProfitIndexChartWidget()
        # 避免误导：用户还没点“开始计算”时不要显示“正在计算...”
        self.chart.status_label.setText("待开始：点击“开始计算”")
        layout.addWidget(self.chart)

        # 事件绑定
        self.start_btn.clicked.connect(self.start_calculation)

        # 读取/应用配置（避免初始化触发保存/调度）
        self._apply_loaded_settings()
        # 你提出“任何修改都应该触发保存”：这里在初始化把当前配置落盘一次，
        # 这样即使你只是启动后才看到界面，也能确保配置文件存在且值正确。
        self._save_settings()

        # 参数改变时保存配置
        self.days_spin.valueChanged.connect(self._on_days_changed)
        self.test_stock_cb.toggled.connect(lambda _checked: self._save_settings())
        self.test_stock_spin.valueChanged.connect(lambda _v: self._save_settings())

    def start_calculation(self):
        if self.profit_index_thread and self.profit_index_thread.isRunning():
            return

        days = int(self.days_spin.value())
        stock_limit = int(self.test_stock_spin.value()) if (self.test_stock_cb.isChecked() and self._thread_supports_stock_limit) else None
        self.start_btn.setEnabled(False)
        # 真实计算还会额外取数据用于均线（auto_limit_up_filter.py 里的线程固定 extra_days=10）
        extra_days = 10
        pool_info = "股票池：沪深A股"
        test_info = f"；测试模式：仅前{stock_limit}只" if stock_limit is not None else ""
        self.chart.status_label.setText(
            f"正在计算赚钱指数...（{pool_info}）\n"
            f"显示最近{days}个交易日；额外取{extra_days}天用于均线{test_info}"
        )

        # 如果线程支持 stock_limit 才传，否则只传 days，确保不会因签名不一致崩溃
        if stock_limit is not None:
            self.profit_index_thread = ProfitIndexCalculatorThread(days=days, stock_limit=stock_limit)
        else:
            self.profit_index_thread = ProfitIndexCalculatorThread(days=days)
        self.profit_index_thread.progress_updated.connect(self.on_progress)
        self.profit_index_thread.calculation_finished.connect(self.on_finished)
        self.profit_index_thread.error_occurred.connect(self.on_error)

        self.profit_index_thread.start()

    def on_progress(self, current, total, stock_code):
        # ProfitIndexChartWidget 自己有 status_label
        self.chart.status_label.setText(f"计算赚钱指数... {current}/{total} ({stock_code})")

    def on_finished(self, data: dict):
        try:
            self.chart.update_chart(data)
        finally:
            self.start_btn.setText("已完成（可重复开始）")
            self.start_btn.setEnabled(True)
            if self._auto_run:
                self._save_auto_run_screenshot_and_exit()

    def on_error(self, error_msg: str):
        self.chart.status_label.setText(f"计算出错: {error_msg}")
        self.start_btn.setText("开始计算")
        self.start_btn.setEnabled(True)

    def closeEvent(self, event):
        # 窗口关闭时停止线程，避免后台继续跑
        try:
            if self.profit_index_thread and self.profit_index_thread.isRunning():
                self.profit_index_thread.stop()
                self.profit_index_thread.wait(2000)
        finally:
            super().closeEvent(event)

    def _apply_loaded_settings(self):
        """
        从 `data/profit_index_settings.json` 读取：
        - days: int
        """
        default_days = int(self.days_spin.value())
        default_test_only = bool(self.test_stock_cb.isChecked())
        default_test_limit_n = int(self.test_stock_spin.value())

        loaded_days = default_days
        loaded_test_only = default_test_only
        loaded_test_limit_n = default_test_limit_n
        try:
            if os.path.exists(self._settings_path):
                with open(self._settings_path, "r", encoding="utf-8") as f:
                    obj = json.load(f) or {}
                # days 用于 QSpinBox 的值
                if "days" in obj:
                    try:
                        loaded_days = int(obj.get("days", loaded_days))
                    except Exception:
                        loaded_days = default_days
                # test_only：是否启用“只取前N只”
                if "test_only" in obj:
                    loaded_test_only = bool(obj.get("test_only", default_test_only))
                # test_limit_n：N 的值
                if "test_limit_n" in obj:
                    try:
                        loaded_test_limit_n = int(obj.get("test_limit_n", default_test_limit_n))
                    except Exception:
                        loaded_test_limit_n = default_test_limit_n
        except Exception:
            # 配置损坏时忽略，走默认值
            loaded_days = default_days

        # 阻断信号避免在初始化阶段保存/调度
        self.days_spin.blockSignals(True)
        self.test_stock_cb.blockSignals(True)
        self.test_stock_spin.blockSignals(True)
        try:
            self.days_spin.setValue(loaded_days)
            # 如果当前线程不支持 stock_limit，则强制禁用测试模式
            if self._thread_supports_stock_limit:
                self.test_stock_cb.setChecked(loaded_test_only)
                self.test_stock_spin.setValue(loaded_test_limit_n)
                self.test_stock_spin.setEnabled(loaded_test_only)
            else:
                self.test_stock_cb.setChecked(False)
                self.test_stock_spin.setValue(loaded_test_limit_n)
                self.test_stock_cb.setEnabled(False)
                self.test_stock_spin.setEnabled(False)
        finally:
            self.days_spin.blockSignals(False)
            self.test_stock_cb.blockSignals(False)
            self.test_stock_spin.blockSignals(False)

        self.chart.status_label.setText("待开始：点击“开始计算”")

    def _save_settings(self):
        try:
            payload = {
                "days": int(self.days_spin.value()),
                "test_only": bool(self.test_stock_cb.isChecked()) if self._thread_supports_stock_limit else False,
                "test_limit_n": int(self.test_stock_spin.value()),
            }

            with open(self._settings_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            # 保存失败不影响运行
            pass

    def _on_days_changed(self, _value: int):
        self._save_settings()

    def _save_auto_run_screenshot_and_exit(self):
        """自动运行模式：保存截图后自动退出程序。"""
        try:
            root = os.path.dirname(os.path.abspath(__file__))
            history_dir = os.path.join(root, "history_data")
            os.makedirs(history_dir, exist_ok=True)
            now = datetime.now()
            date_str = now.strftime("%Y-%m-%d")
            time_str = now.strftime("%H%M%S")
            screenshot_path = os.path.join(history_dir, f"赚钱指数_{date_str}_{time_str}.png")

            # 截图保存整个对话框（含图表与状态）
            pix = self.grab()
            ok = pix.save(screenshot_path, "PNG")
            if ok:
                self.chart.status_label.setText(f"自动运行完成，截图已保存：{screenshot_path}")
            else:
                self.chart.status_label.setText("自动运行完成，但截图保存失败。")
        except Exception as e:
            self.chart.status_label.setText(f"自动运行完成，但截图异常：{e}")
        finally:
            QTimer.singleShot(10000, self.accept)

def main():
    print_concept_stock_summary()
    parser = argparse.ArgumentParser(description="赚钱指数（独立版）")
    parser.add_argument(
        "--auto-run",
        action="store_true",
        help="启动后自动按当前参数计算；完成后自动截图并退出",
    )
    args, _unknown = parser.parse_known_args()
    app = QApplication(sys.argv)
    dlg = ProfitIndexDialog(days=30, auto_run=bool(args.auto_run))
    dlg.show()
    if args.auto_run:
        # 等待窗口初始化完成后启动，使用当前界面参数（含已加载设置）
        QTimer.singleShot(300, dlg.start_calculation)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

