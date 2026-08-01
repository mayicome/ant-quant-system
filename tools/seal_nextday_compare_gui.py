#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
封单结构 vs 次日表现 对照 GUI（单日）：
- 默认使用上一个交易日的「封单结构_YYYYMMDD.xlsx」
- 自动生成对应单日「次日字段通用_YYYYMMDD.xlsx」
- 合并展示「昨日涨停今日表现」与评级吻合度
"""

from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
from PyQt5.QtCore import QDate, Qt
from PyQt5.QtWidgets import (
    QApplication,
    QDateEdit,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from tools.build_nextday_universe import (  # noqa: E402
    build_for_one_date,
    get_trading_dates_range,
    history_dir,
    last_settled_trading_day,
    list_limitup_csv_files,
)
from tools.seal_structure_rolling_stats import (  # noqa: E402
    RATING_ORDER,
    find_column,
    _prepare_seal_frame,
    load_nextday_universe,
)


def _list_seal_dates() -> List[str]:
    hd = history_dir()
    out: List[str] = []
    if not os.path.isdir(hd):
        return out
    for name in os.listdir(hd):
        if not name.lower().endswith(".xlsx"):
            continue
        if not name.startswith("封单结构_"):
            continue
        if ("含次日" in name) or ("滚动检验" in name) or ("评估" in name) or ("参数寻优" in name):
            continue
        m = re.search(r"(\d{8})", name)
        if m:
            out.append(m.group(1))
    out = sorted(set(out))
    return out


def _seal_file_by_date(day: str) -> str:
    return os.path.join(history_dir(), f"封单结构_{day}.xlsx")


def _default_date() -> str:
    dates = _list_seal_dates()
    if not dates:
        return datetime.now().strftime("%Y%m%d")
    settled = last_settled_trading_day()
    if not settled:
        return dates[-1]
    cal = get_trading_dates_range(
        (datetime.strptime(settled, "%Y%m%d")).strftime("%Y%m01"),
        settled,
    )
    if settled in cal:
        idx = cal.index(settled)
        if idx > 0:
            prev = cal[idx - 1]
            if prev in dates:
                return prev
    candidates = [d for d in dates if d < settled]
    return candidates[-1] if candidates else dates[-1]


def _csv_map() -> Dict[str, str]:
    return {d: fp for d, fp in list_limitup_csv_files(history_dir())}


def _fmt(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def _extract_name_map(raw_df: pd.DataFrame) -> pd.DataFrame:
    """从封单原表提取 code->name 映射。"""
    code_col = find_column(raw_df, ["股票代码", "代码", "code"])
    name_col = find_column(raw_df, ["股票名称", "名称", "name"])
    if code_col is None:
        return pd.DataFrame(columns=["code", "name"])
    x = pd.DataFrame()
    x["code"] = (
        raw_df[code_col]
        .astype(str)
        .str.extract(r"(\d{6})", expand=False)
        .fillna("")
        .str.zfill(6)
    )
    x["name"] = raw_df[name_col].astype(str).str.strip() if name_col is not None else ""
    x = x[(x["code"] != "") & (x["name"] != "")]
    x = x.drop_duplicates(subset=["code"], keep="first")
    return x.reset_index(drop=True)


def _evaluate_day(merged: pd.DataFrame, target_th: float = -1.0) -> Tuple[pd.DataFrame, str]:
    x = merged.copy()
    x["rating_score"] = pd.to_numeric(x.get("rating_score"), errors="coerce")
    x = x.dropna(subset=["rating_score", "next_day_ret"])
    if x.empty:
        return x, "无可评估样本（合并后为空）。"
    x["rating_score"] = x["rating_score"].astype(int)

    # 吻合定义：高评级(>=4) 期望次日 > target_th；低评级(<=2) 期望次日 <= target_th
    def _match(row) -> str:
        rs = int(row["rating_score"])
        ret = float(row["next_day_ret"])
        if rs >= 4:
            return "吻合" if ret > target_th else "不吻合"
        if rs <= 2:
            return "吻合" if ret <= target_th else "不吻合"
        return "中性"

    x["match_flag"] = x.apply(_match, axis=1)
    eval_scope = x[x["rating_score"].isin([1, 2, 4, 5])]
    match_rate = (eval_scope["match_flag"] == "吻合").mean() if not eval_scope.empty else float("nan")
    spearman = x["rating_score"].corr(x["next_day_ret"], method="spearman") if len(x) >= 3 else float("nan")
    high = x[x["rating_score"] >= 4]
    low = x[x["rating_score"] <= 2]
    high_med = high["next_day_ret"].median() if not high.empty else float("nan")
    low_med = low["next_day_ret"].median() if not low.empty else float("nan")
    high_up = (high["next_day_ret"] > target_th).mean() if not high.empty else float("nan")
    low_up = (low["next_day_ret"] > target_th).mean() if not low.empty else float("nan")

    ok = 0
    if pd.notna(spearman) and spearman > 0:
        ok += 1
    if pd.notna(high_med) and pd.notna(low_med) and high_med >= low_med:
        ok += 1
    if pd.notna(high_up) and pd.notna(low_up) and high_up >= low_up:
        ok += 1
    if pd.notna(match_rate) and match_rate >= 0.55:
        ok += 1
    verdict = "结论：昨日评级对今日表现有正向区分能力（可用）。" if ok >= 3 else "结论：区分能力偏弱，建议继续观察样本。"

    text = (
        f"样本数: {len(x)}\n"
        f"Spearman(评级序数, 次日涨幅): {spearman:.4f}\n"
        f"高评级中位数(>=🟢): {high_med:.2f}% | 低评级中位数(<=🟠): {low_med:.2f}%\n"
        f"高评级成功率(ret>{target_th:.1f}%): {high_up:.2%} | 低评级成功率: {low_up:.2%}\n"
        f"吻合率(高/低评级口径): {match_rate:.2%}\n"
        f"{verdict}"
    )
    return x, text


class SealNextdayCompareWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("封单结构次日对照")
        self.resize(1180, 760)
        self._build_ui()
        self._load_default()

    def _build_ui(self) -> None:
        c = QWidget(self)
        self.setCentralWidget(c)
        v = QVBoxLayout(c)

        top = QHBoxLayout()
        top.addWidget(QLabel("封单结构日期:"))
        self.date_edit = QDateEdit(self)
        self.date_edit.setDisplayFormat("yyyyMMdd")
        self.date_edit.setCalendarPopup(True)
        top.addWidget(self.date_edit)

        self.seal_path_label = QLabel("")
        self.seal_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(self.seal_path_label, 1)

        self.pick_btn = QPushButton("选择封单文件", self)
        self.pick_btn.clicked.connect(self._pick_file)
        top.addWidget(self.pick_btn)

        self.run_btn = QPushButton("生成并对照", self)
        self.run_btn.clicked.connect(self._run_compare)
        top.addWidget(self.run_btn)
        v.addLayout(top)

        self.table = QTableWidget(self)
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(
            ["代码", "名称", "封单评级", "置信度", "次日涨幅%", "次日是否涨停", "评级分", "吻合", "封单文件日期"]
        )
        v.addWidget(self.table, 1)

        self.out_text = QTextEdit(self)
        self.out_text.setReadOnly(True)
        self.out_text.setMinimumHeight(200)
        v.addWidget(self.out_text)

    def _load_default(self) -> None:
        d = _default_date()
        qd = QDate.fromString(d, "yyyyMMdd")
        if qd.isValid():
            self.date_edit.setDate(qd)
        self._update_seal_label()

    def _update_seal_label(self) -> None:
        d = self.date_edit.date().toString("yyyyMMdd")
        fp = _seal_file_by_date(d)
        self.seal_path_label.setText(fp if os.path.isfile(fp) else f"未找到: {fp}")

    def _pick_file(self) -> None:
        fp, _ = QFileDialog.getOpenFileName(self, "选择封单结构文件", history_dir(), "Excel (*.xlsx)")
        if not fp:
            return
        m = re.search(r"(\d{8})", os.path.basename(fp))
        if m:
            qd = QDate.fromString(m.group(1), "yyyyMMdd")
            if qd.isValid():
                self.date_edit.setDate(qd)
        self.seal_path_label.setText(fp)

    def _run_compare(self) -> None:
        day = self.date_edit.date().toString("yyyyMMdd")
        seal_fp = _seal_file_by_date(day)
        if not os.path.isfile(seal_fp):
            QMessageBox.warning(self, "缺少文件", f"未找到封单结构文件:\n{seal_fp}")
            return

        csv_fp = _csv_map().get(day)
        if not csv_fp or (not os.path.isfile(csv_fp)):
            QMessageBox.warning(self, "缺少文件", f"未找到对应涨停板CSV（{day}）")
            return

        settled = last_settled_trading_day()
        if not settled:
            QMessageBox.warning(self, "QMT异常", "无法获取已收盘交易日，请检查 QMT/xtquant。")
            return

        try:
            nextday_df, msg = build_for_one_date(day, csv_fp, settled)
        except Exception as e:
            QMessageBox.critical(self, "生成失败", f"次日字段生成异常:\n{e}")
            return
        if nextday_df.empty:
            self.out_text.setPlainText(f"无法生成次日字段：{msg}")
            self.table.setRowCount(0)
            return

        nextday_out = os.path.join(history_dir(), f"次日字段通用_{day}.xlsx")
        nextday_df.to_excel(nextday_out, index=False)

        try:
            seal_raw = pd.read_excel(seal_fp)
            seal_df = _prepare_seal_frame(seal_raw, day)
            name_df = _extract_name_map(seal_raw)
            if not name_df.empty:
                seal_df = seal_df.merge(name_df, on="code", how="left")
            else:
                seal_df["name"] = ""
            nd_std = load_nextday_universe(nextday_out)
            merged = seal_df.merge(
                nd_std[["file_date", "code", "next_day_ret", "next_day_limit_up"]],
                on=["file_date", "code"],
                how="left",
            )
            merged = merged.dropna(subset=["next_day_ret"]).reset_index(drop=True)
            eval_df, conclusion = _evaluate_day(merged, target_th=-1.0)
        except Exception as e:
            QMessageBox.critical(self, "合并失败", f"合并或评估异常:\n{e}")
            return

        self._fill_table(eval_df)
        self.out_text.setPlainText(
            f"封单文件: {os.path.basename(seal_fp)}\n"
            f"次日字段: {os.path.basename(nextday_out)}\n"
            f"{msg}\n\n{conclusion}"
        )

    def _fill_table(self, df: pd.DataFrame) -> None:
        cols = [
            ("code", Qt.AlignCenter),
            ("name", Qt.AlignLeft | Qt.AlignVCenter),
            ("seal_rating", Qt.AlignLeft | Qt.AlignVCenter),
            ("confidence_tag", Qt.AlignCenter),
            ("next_day_ret", Qt.AlignRight | Qt.AlignVCenter),
            ("next_day_limit_up", Qt.AlignCenter),
            ("rating_score", Qt.AlignCenter),
            ("match_flag", Qt.AlignCenter),
            ("file_date", Qt.AlignCenter),
        ]
        self.table.setRowCount(len(df))
        for r, row in df.iterrows():
            for c, (k, align) in enumerate(cols):
                v = row.get(k)
                if k == "next_day_limit_up":
                    if pd.isna(v):
                        txt = ""
                    else:
                        txt = "是" if bool(v) else "否"
                else:
                    txt = _fmt(v)
                it = QTableWidgetItem(txt)
                it.setTextAlignment(align)
                self.table.setItem(r, c, it)
        self.table.resizeColumnsToContents()


def main() -> None:
    app = QApplication(sys.argv)
    w = SealNextdayCompareWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

