#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
主线分组独立 GUI：
- 从 history_data/concept 读取最近5个 concept_summary_YYYY-MM-DD.xlsx
- 计算五日综合得分并给出主线/次主线/一日游分组
- 可选保存 concept/concept_rank_YYYY-MM-DD.xlsx
"""

import os
import re
import sys
import json
import math
import argparse
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd
from PyQt5.QtCore import QDate, Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QDateEdit,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

try:
    import xtquant.xtdata as xtdata
except Exception:
    xtdata = None
else:
    try:
        xtdata.enable_hello = False
    except Exception:
        pass


class MainLineGroupDialog(QDialog):
    def __init__(self, parent=None, auto_run: bool = False):
        super().__init__(parent)
        self.setWindowTitle("蚂蚁量化 - 主线分组（独立版）")
        self.resize(920, 700)
        self._history_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "history_data")
        self._auto_run = bool(auto_run)
        self._last_leader_text_path: str = ""

        layout = QVBoxLayout(self)

        top_row = QHBoxLayout()
        layout.addLayout(top_row)
        self.status_label = QLabel("待执行：点击“读取近五日并生成主线分组”")
        top_row.addWidget(self.status_label, 1)

        top_row.addWidget(QLabel("计算日期:"))
        self.calc_date_edit = QDateEdit(self)
        self.calc_date_edit.setCalendarPopup(True)
        self.calc_date_edit.setDisplayFormat("yyyy-MM-dd")
        self.calc_date_edit.setDate(QDate.currentDate())
        top_row.addWidget(self.calc_date_edit)

        self.save_rank_cb = QCheckBox("同时保存 concept_rank 文件")
        self.save_rank_cb.setChecked(True)
        top_row.addWidget(self.save_rank_cb)

        self.run_btn = QPushButton("读取近五日并生成主线分组")
        top_row.addWidget(self.run_btn)
        self.leader_btn = QPushButton("生成主线核心标的")
        top_row.addWidget(self.leader_btn)

        self.result_edit = QTextEdit(self)
        self.result_edit.setReadOnly(True)
        self.result_edit.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        layout.addWidget(self.result_edit, 1)

        self._last_rank_df: Optional[pd.DataFrame] = None
        self._last_recent_pairs: List[Tuple[str, str]] = []

        self.run_btn.clicked.connect(self.refresh_group_result)
        self.leader_btn.clicked.connect(self.generate_main_line_leaders)

    def _save_result_text(self, filename: str, content: str) -> str:
        """将结果文本保存到 history_data，返回完整路径。"""
        os.makedirs(self._history_dir, exist_ok=True)
        out_fp = os.path.join(self._history_dir, filename)
        with open(out_fp, "w", encoding="utf-8") as f:
            f.write(content or "")
        return out_fp

    @staticmethod
    def _normalize_stock_code(code: object) -> str:
        s = str(code or "").strip().upper()
        if not s:
            return ""
        m = re.search(r"(\d{6})", s)
        return m.group(1) if m else ""

    @staticmethod
    def _to_float(v: object, default: float = 0.0) -> float:
        try:
            if v is None:
                return default
            return float(v)
        except Exception:
            return default

    @staticmethod
    def _to_xt_code(code: object) -> str:
        """
        统一为 xtdata 常见格式：000001.SZ / 600000.SH / 430001.BJ。
        """
        s = str(code or "").strip().upper()
        if not s:
            return ""
        # 已是 000001.SZ 这类格式
        m = re.search(r"(\d{6})\.(SH|SZ|BJ)$", s)
        if m:
            return f"{m.group(1)}.{m.group(2)}"
        # SH600000 / SZ000001 / BJ430001
        m = re.search(r"^(SH|SZ|BJ)(\d{6})$", s)
        if m:
            return f"{m.group(2)}.{m.group(1)}"
        # 纯6位代码时按首位推断市场
        m = re.search(r"(\d{6})", s)
        if not m:
            return ""
        d6 = m.group(1)
        if d6.startswith(("6", "9")):
            ex = "SH"
        elif d6.startswith(("4", "8")):
            ex = "BJ"
        else:
            ex = "SZ"
        return f"{d6}.{ex}"

    @staticmethod
    def _to_voice_stock_code(code: object) -> str:
        """
        语音播报友好格式：SH.600010（交易所前缀在前，避免纯 6 位数字被念成数值）。
        """
        s = str(code or "").strip().upper()
        if not s:
            return ""
        m = re.search(r"^(SH|SZ|BJ)\.(\d{6})$", s)
        if m:
            return f"{m.group(1)}.{m.group(2)}"
        xt = MainLineGroupDialog._to_xt_code(code)
        if not xt or "." not in xt:
            return xt or s
        d6, ex = xt.split(".", 1)
        return f"{ex}.{d6}"

    def _load_stock_info(self) -> Tuple[Dict[str, str], Dict[str, List[str]], Dict[str, set]]:
        """
        返回：
        - code_to_name
        - code_to_concepts
        - concept_to_codes
        """
        code_to_name: Dict[str, str] = {}
        code_to_concepts: Dict[str, List[str]] = {}
        concept_to_codes: Dict[str, set] = {}
        fp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "all_a_stock_info.json")
        if not os.path.exists(fp):
            return code_to_name, code_to_concepts, concept_to_codes
        try:
            with open(fp, "r", encoding="utf-8") as f:
                obj = json.load(f) or {}
            if not isinstance(obj, dict):
                return code_to_name, code_to_concepts, concept_to_codes
            for k, v in obj.items():
                if not isinstance(v, dict):
                    continue
                code = self._normalize_stock_code(v.get("stock_code") or k)
                if not code:
                    continue
                name = str(v.get("name") or "").strip()
                concepts = [str(x or "").strip() for x in (v.get("concepts") or []) if str(x or "").strip()]
                code_to_name[code] = name
                code_to_concepts[code] = concepts
                for c in concepts:
                    concept_to_codes.setdefault(c, set()).add(code)
        except Exception:
            pass
        return code_to_name, code_to_concepts, concept_to_codes

    def _find_daily_change_clean_by_date(self, d8: str) -> Optional[str]:
        ds = f"{d8[:4]}-{d8[4:6]}-{d8[6:8]}"
        fp = os.path.join(self._history_dir, f"daily_change_clean_{ds}.xlsx")
        return fp if os.path.exists(fp) else None

    def _find_latest_daily_change_clean_file(self) -> Optional[str]:
        if not os.path.isdir(self._history_dir):
            return None
        names = [n for n in os.listdir(self._history_dir) if n.startswith("daily_change_clean_") and n.endswith(".xlsx")]
        if not names:
            return None
        names.sort()
        return os.path.join(self._history_dir, names[-1])

    def _list_recent_concept_summary_files(self) -> List[Tuple[str, str]]:
        from utils.concept_path import list_concept_summary_files

        return list_concept_summary_files(self._history_dir)

    def _build_rank_from_summary_files(self, recent_pairs: List[Tuple[str, str]]) -> Optional[pd.DataFrame]:
        frames: List[pd.DataFrame] = []
        for d8, fp in recent_pairs:
            try:
                df = pd.read_excel(fp)
                if df is None or df.empty or ("concept" not in df.columns):
                    continue
                need_cols = ["concept", "avg_change_pct", "limit_up_density", "up_ratio"]
                if any(c not in df.columns for c in need_cols):
                    continue
                day_df = df[need_cols].copy()
                day_df["trade_date"] = d8
                frames.append(day_df)
            except Exception:
                continue

        if not frames:
            return None

        all_df = pd.concat(frames, ignore_index=True)
        for c in ["avg_change_pct", "limit_up_density", "up_ratio"]:
            all_df[c] = pd.to_numeric(all_df[c], errors="coerce").fillna(0.0)

        latest_d8 = recent_pairs[-1][0]
        grp = all_df.groupby("concept", as_index=False).agg(
            five_day_sum_chg=("avg_change_pct", "sum"),
            five_day_avg_zt=("limit_up_density", "mean"),
            five_day_avg_up=("up_ratio", "mean"),
            day_count=("trade_date", "nunique"),
            single_day_max_chg=("avg_change_pct", "max"),
        )
        today_df = (
            all_df[all_df["trade_date"] == latest_d8][["concept", "avg_change_pct"]]
            .rename(columns={"avg_change_pct": "today_avg_change_pct"})
            .drop_duplicates(subset=["concept"])
        )
        grp = grp.merge(today_df, on="concept", how="left")
        grp["today_avg_change_pct"] = pd.to_numeric(
            grp["today_avg_change_pct"], errors="coerce"
        ).fillna(0.0)
        grp["composite_score"] = (
            grp["five_day_sum_chg"] * 0.40 + grp["five_day_avg_zt"] * 0.35 + grp["five_day_avg_up"] * 0.25
        )
        top10_today_concepts = set(
            grp.sort_values("today_avg_change_pct", ascending=False)
            .head(10)["concept"]
            .astype(str)
            .tolist()
        )
        grp["is_one_day_rotation"] = (
            grp["concept"].astype(str).isin(top10_today_concepts)
            & (grp["five_day_sum_chg"] < 8.0)
            & (grp["five_day_avg_up"] < 0.6)
        )
        grp = grp.sort_values("composite_score", ascending=False).reset_index(drop=True)
        grp.insert(0, "rank", grp.index + 1)
        grp["as_of_date"] = f"{latest_d8[:4]}-{latest_d8[4:6]}-{latest_d8[6:8]}"
        return grp

    def _group_rank_lines(self, rank_df: pd.DataFrame) -> List[str]:
        top_main = rank_df.head(3)["concept"].astype(str).tolist()
        top_sub = rank_df.iloc[3:6]["concept"].astype(str).tolist()
        main_pool = set(top_main + top_sub)
        one_day: List[str] = []
        if "is_one_day_rotation" in rank_df.columns:
            one_day = [
                c for c in rank_df[rank_df["is_one_day_rotation"] == True]["concept"].astype(str).tolist()  # noqa: E712
                if c not in main_pool
            ]
        return [
            f"最强主线 TOP1-3: {', '.join(top_main) if top_main else '无'}",
            f"次主线 TOP4-6: {', '.join(top_sub) if top_sub else '无'}",
            f"一日游或新主线: {', '.join(one_day) if one_day else '无'}",
        ]

    def _render_group_text(self, rank_df: pd.DataFrame, used_files: List[str]) -> str:
        lines = [
            "主线分组结果",
            f"数据文件（最近{len(used_files)}天）: {', '.join(used_files)}",
            "",
        ] + self._group_rank_lines(rank_df)
        return "\n".join(lines)

    def refresh_group_result(self):
        self.run_btn.setEnabled(False)
        try:
            all_pairs = self._list_recent_concept_summary_files()
            target_d8 = self.calc_date_edit.date().toString("yyyyMMdd")
            valid_pairs = [p for p in all_pairs if p[0] <= target_d8]
            if len(valid_pairs) < 5:
                self.status_label.setText(
                    f"截至 {target_d8[:4]}-{target_d8[4:6]}-{target_d8[6:8]} 的 concept_summary 不足5天（当前 {len(valid_pairs)} 天）"
                )
                self.result_edit.setPlainText("未生成分组：截至所选日期，可用 concept_summary 文件不足5个交易日。")
                return

            recent_pairs = valid_pairs[-5:]
            rank_df = self._build_rank_from_summary_files(recent_pairs)
            if rank_df is None or rank_df.empty:
                self.status_label.setText("五日分组生成失败：文件缺列或内容为空")
                self.result_edit.setPlainText("未生成分组：近五日 concept_summary 文件读取失败或字段不完整。")
                return

            used_files = [os.path.basename(fp) for _, fp in recent_pairs]
            msg = self._render_group_text(rank_df, used_files)
            self.result_edit.setPlainText(msg)
            self._last_rank_df = rank_df.copy()
            self._last_recent_pairs = list(recent_pairs)

            latest_d8 = recent_pairs[-1][0]
            text_name = f"main_line_group_{latest_d8[:4]}-{latest_d8[4:6]}-{latest_d8[6:8]}.txt"
            self._save_result_text(text_name, msg)
            if self.save_rank_cb.isChecked():
                from utils.concept_path import concept_rank_path, ensure_concept_data_dir

                ensure_concept_data_dir(self._history_dir)
                out_fp = concept_rank_path(latest_d8, self._history_dir)
                out_name = os.path.basename(out_fp)
                rank_df.to_excel(out_fp, index=False)
                self.status_label.setText(f"分组完成，已保存：concept/{out_name}；{text_name}")
            else:
                self.status_label.setText(
                    f"分组完成（截至 {latest_d8[:4]}-{latest_d8[4:6]}-{latest_d8[6:8]}，未保存 concept_rank，已保存 {text_name}）"
                )
        except Exception as e:
            self.status_label.setText(f"分组失败：{e}")
            QMessageBox.warning(self, "主线分组失败", str(e))
        finally:
            self.run_btn.setEnabled(True)

    def _fetch_tech_metrics(self, stock_codes: List[str]) -> Dict[str, Dict[str, float]]:
        """
        通过 xtdata 拉取日线，构建连板/趋势指标。
        若 xtdata 不可用，返回空指标（后续按 0 处理）。
        """
        metrics: Dict[str, Dict[str, float]] = {c: {} for c in stock_codes}
        if xtdata is None or (not stock_codes):
            return metrics

        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=80)).strftime("%Y%m%d")

        for code in stock_codes:
            try:
                full_code = self._to_xt_code(code)
                if not full_code:
                    continue
                try:
                    xtdata.download_history_data(full_code, "1d", start_date, end_date)
                except Exception:
                    pass
                data = xtdata.get_market_data_ex([], [full_code], period="1d", start_time=start_date, end_time=end_date, count=-1)
                if not data or full_code not in data or data[full_code] is None or data[full_code].empty:
                    continue
                df = data[full_code]
                if "close" not in df.columns:
                    continue
                close = pd.to_numeric(df["close"], errors="coerce").dropna()
                close = close[close > 0]
                if close.empty:
                    continue

                ret = close.pct_change().fillna(0.0) * 100.0
                ret_tail = ret.tail(20)
                cont_board = 0
                for x in reversed(ret_tail.tolist()):
                    if x >= 9.8:
                        cont_board += 1
                    else:
                        break
                board_count_10d = float((ret.tail(10) >= 9.8).sum())

                chg_5d = 0.0
                if len(close) >= 6 and close.iloc[-6] != 0:
                    chg_5d = (close.iloc[-1] / close.iloc[-6] - 1.0) * 100.0

                ma5 = close.rolling(5).mean().iloc[-1] if len(close) >= 5 else close.iloc[-1]
                ma10 = close.rolling(10).mean().iloc[-1] if len(close) >= 10 else ma5
                ma20 = close.rolling(20).mean().iloc[-1] if len(close) >= 20 else ma10
                last = close.iloc[-1]
                ma_align = 0.0
                ma_align += 1.0 if last > ma5 else 0.0
                ma_align += 1.0 if ma5 > ma10 else 0.0
                ma_align += 1.0 if ma10 > ma20 else 0.0

                drawdown_10d = 0.0
                c10 = close.tail(10)
                if len(c10) >= 2:
                    peak = c10.cummax()
                    dd = (c10 / peak - 1.0) * 100.0
                    drawdown_10d = float(abs(dd.min()))

                metrics[code] = {
                    "cont_board": float(cont_board),
                    "board_count_10d": board_count_10d,
                    "board_count_20d": float((ret.tail(20) >= 9.8).sum()),
                    "chg_5d": float(chg_5d),
                    "ma_align": float(ma_align),
                    "drawdown_10d": float(drawdown_10d),
                    "ret_today": float(ret.iloc[-1]) if len(ret) >= 1 else 0.0,
                    "ret_yday": float(ret.iloc[-2]) if len(ret) >= 2 else 0.0,
                }
            except Exception:
                continue
        return metrics

    def _build_candidate_dataframe(self, rank_df: pd.DataFrame, latest_d8: str) -> Tuple[Optional[pd.DataFrame], str]:
        """
        主线候选池：最强主线 TOP1-3 对应概念成分股，与当日 daily_change_clean 交集。
        每只票会展开到其所属的 top3 概念（多概念票可重复多行），便于“每条主线分别选龙头”。
        """
        if rank_df is None or rank_df.empty or ("concept" not in rank_df.columns):
            return None, "缺少可用的主线排名数据。"

        top_main = rank_df.head(3)["concept"].astype(str).tolist()
        target_concepts = [c for c in top_main if c]
        if not target_concepts:
            return None, "主线概念为空，无法构建候选池。"

        code_to_name, code_to_concepts, concept_to_codes = self._load_stock_info()
        if not code_to_name:
            return None, "未找到 data/all_a_stock_info.json，无法反算概念成分股。"

        daily_fp = self._find_daily_change_clean_by_date(latest_d8) or self._find_latest_daily_change_clean_file()
        if not daily_fp:
            return None, "未找到 daily_change_clean_*.xlsx 文件。"
        try:
            daily_df = pd.read_excel(daily_fp)
        except Exception as e:
            return None, f"读取日涨幅文件失败: {e}"
        if daily_df is None or daily_df.empty or ("stock_code" not in daily_df.columns):
            return None, f"日涨幅文件无有效 stock_code 列: {os.path.basename(daily_fp)}"

        daily_df = daily_df.copy()
        daily_df["stock_code"] = daily_df["stock_code"].map(self._normalize_stock_code)
        daily_df = daily_df[daily_df["stock_code"] != ""].reset_index(drop=True)
        daily_df["change_pct"] = pd.to_numeric(daily_df.get("change_pct"), errors="coerce").fillna(0.0)
        if "amount_yi" not in daily_df.columns:
            if "amount" in daily_df.columns:
                daily_df["amount_yi"] = pd.to_numeric(daily_df.get("amount"), errors="coerce").fillna(0.0) / 1e8
            else:
                daily_df["amount_yi"] = 0.0
        daily_df["amount_yi"] = pd.to_numeric(daily_df.get("amount_yi"), errors="coerce").fillna(0.0)

        candidate_codes: set = set()
        for c in target_concepts:
            candidate_codes |= set(concept_to_codes.get(c, set()))

        if not candidate_codes:
            return None, "主线概念未匹配到任何成分股。"

        cand = daily_df[daily_df["stock_code"].isin(candidate_codes)].copy()
        if cand.empty:
            return None, "主线成分股与当日 daily_change_clean 无交集。"

        rows: List[Dict[str, object]] = []
        concept_rank_order = {c: i for i, c in enumerate(target_concepts)}
        for _, r in cand.iterrows():
            code = str(r.get("stock_code", ""))
            stock_name = code_to_name.get(code, "")
            concepts = [x for x in code_to_concepts.get(code, []) if x in target_concepts]
            for cpt in concepts:
                row = dict(r)
                row["stock_code"] = code
                row["stock_name"] = stock_name
                row["main_concept"] = cpt
                row["concept_rank_order"] = concept_rank_order.get(cpt, 999)
                row["theme_score"] = max(0.0, 3.0 - float(row["concept_rank_order"]))
                rows.append(row)

        if not rows:
            return None, "主线成分股与 TOP1-3 概念映射失败。"

        cand = pd.DataFrame(rows).reset_index(drop=True)

        tech = self._fetch_tech_metrics(cand["stock_code"].astype(str).tolist())
        cand["cont_board"] = cand["stock_code"].map(lambda x: self._to_float(tech.get(str(x), {}).get("cont_board"), 0.0))
        cand["board_count_10d"] = cand["stock_code"].map(lambda x: self._to_float(tech.get(str(x), {}).get("board_count_10d"), 0.0))
        cand["board_count_20d"] = cand["stock_code"].map(lambda x: self._to_float(tech.get(str(x), {}).get("board_count_20d"), 0.0))
        cand["chg_5d"] = cand["stock_code"].map(lambda x: self._to_float(tech.get(str(x), {}).get("chg_5d"), 0.0))
        cand["ma_align"] = cand["stock_code"].map(lambda x: self._to_float(tech.get(str(x), {}).get("ma_align"), 0.0))
        cand["drawdown_10d"] = cand["stock_code"].map(lambda x: self._to_float(tech.get(str(x), {}).get("drawdown_10d"), 0.0))
        cand["ret_today"] = cand["stock_code"].map(lambda x: self._to_float(tech.get(str(x), {}).get("ret_today"), cand.loc[cand["stock_code"] == x, "change_pct"].head(1).values[0] if (cand["stock_code"] == x).any() else 0.0))
        cand["ret_yday"] = cand["stock_code"].map(lambda x: self._to_float(tech.get(str(x), {}).get("ret_yday"), 0.0))

        cand["board_score"] = cand["cont_board"] * 25.0 + cand["board_count_10d"] * 8.0 + cand["change_pct"].clip(lower=0.0) * 0.5
        cand["trend_score"] = cand["chg_5d"] * 2.0 + cand["ma_align"] * 12.0 - cand["drawdown_10d"] * 0.8
        cand["core_score"] = cand["amount_yi"].map(lambda x: math.log1p(max(float(x), 0.0)) * 12.0) + cand["ma_align"] * 5.0
        cand["low_rebound_score"] = (
            (cand["cont_board"] <= 1).astype(float) * 20.0
            + (cand["board_count_10d"] <= 2).astype(float) * 15.0
            + cand["change_pct"].clip(lower=0.0, upper=15.0) * 1.5
            + cand["chg_5d"].clip(lower=0.0, upper=25.0) * 1.0
            + cand["ma_align"] * 8.0
            - cand["drawdown_10d"] * 0.3
        )
        cand["first_board_score"] = (
            (cand["ret_today"] >= 9.8).astype(float) * 40.0
            + (cand["ret_yday"] < 9.8).astype(float) * 20.0
            + (cand["board_count_20d"] <= 2).astype(float) * 15.0
            + cand["change_pct"].clip(lower=0.0, upper=12.0) * 1.5
            + cand["ma_align"] * 5.0
        )
        cand["total_score"] = (
            cand["board_score"] * 0.35 + cand["trend_score"] * 0.30 + cand["core_score"] * 0.20 + cand["theme_score"] * 0.15
        )
        return cand.sort_values("total_score", ascending=False).reset_index(drop=True), os.path.basename(daily_fp)

    def _pick_role_rows_by_concept(self, cand: pd.DataFrame, concepts: List[str]) -> pd.DataFrame:
        def _best(df: pd.DataFrame, col: str) -> pd.Series:
            if df is None or df.empty:
                return pd.Series(dtype=object)
            return df.sort_values(col, ascending=False).iloc[0]

        rows: List[Dict[str, object]] = []
        for concept in concepts:
            one = cand[cand["main_concept"] == concept].copy()
            if one.empty:
                continue

            r_total = _best(one, "total_score")
            if not r_total.empty:
                rows.append(
                    {
                        "concept_group": concept,
                        "role": "总龙头",
                        "stock_code": r_total.get("stock_code", ""),
                        "stock_name": r_total.get("stock_name", ""),
                        "main_concept": r_total.get("main_concept", ""),
                        "score": float(r_total.get("total_score", 0.0)),
                        "reason": f"综合分最高，连板={r_total.get('cont_board', 0):.0f}，5日涨幅={r_total.get('chg_5d', 0):.2f}%",
                    }
                )
            r_core = _best(one, "core_score")
            if not r_core.empty:
                rows.append(
                    {
                        "concept_group": concept,
                        "role": "中军龙头",
                        "stock_code": r_core.get("stock_code", ""),
                        "stock_name": r_core.get("stock_name", ""),
                        "main_concept": r_core.get("main_concept", ""),
                        "score": float(r_core.get("core_score", 0.0)),
                        "reason": f"容量与稳定性最强，成交额(亿)={r_core.get('amount_yi', 0):.2f}，均线强度={r_core.get('ma_align', 0):.0f}",
                    }
                )
            r_board = _best(one, "board_score")
            if not r_board.empty:
                rows.append(
                    {
                        "concept_group": concept,
                        "role": "连板龙头",
                        "stock_code": r_board.get("stock_code", ""),
                        "stock_name": r_board.get("stock_name", ""),
                        "main_concept": r_board.get("main_concept", ""),
                        "score": float(r_board.get("board_score", 0.0)),
                        "reason": f"连板强度最高，连续涨停={r_board.get('cont_board', 0):.0f}，10日涨停数={r_board.get('board_count_10d', 0):.0f}",
                    }
                )
            r_trend = _best(one, "trend_score")
            if not r_trend.empty:
                rows.append(
                    {
                        "concept_group": concept,
                        "role": "趋势龙头",
                        "stock_code": r_trend.get("stock_code", ""),
                        "stock_name": r_trend.get("stock_name", ""),
                        "main_concept": r_trend.get("main_concept", ""),
                        "score": float(r_trend.get("trend_score", 0.0)),
                        "reason": f"趋势得分最高，5日涨幅={r_trend.get('chg_5d', 0):.2f}%，10日回撤={r_trend.get('drawdown_10d', 0):.2f}%",
                    }
                )
            low_pool = one[(one["cont_board"] <= 1) & (one["chg_5d"] >= 0)].copy()
            if low_pool.empty:
                low_pool = one.copy()
            r_low = _best(low_pool, "low_rebound_score")
            if not r_low.empty:
                rows.append(
                    {
                        "concept_group": concept,
                        "role": "低位补涨龙",
                        "stock_code": r_low.get("stock_code", ""),
                        "stock_name": r_low.get("stock_name", ""),
                        "main_concept": r_low.get("main_concept", ""),
                        "score": float(r_low.get("low_rebound_score", 0.0)),
                        "reason": f"低位补涨特征最优，连板={r_low.get('cont_board', 0):.0f}，今日涨幅={r_low.get('change_pct', 0):.2f}%，5日涨幅={r_low.get('chg_5d', 0):.2f}%",
                    }
                )
            first_pool = one[(one["ret_today"] >= 9.6) & (one["ret_yday"] < 9.6)].copy()
            if first_pool.empty:
                first_pool = one[one["board_count_10d"] <= 1].copy()
            if first_pool.empty:
                first_pool = one.copy()
            r_first = _best(first_pool, "first_board_score")
            if not r_first.empty:
                rows.append(
                    {
                        "concept_group": concept,
                        "role": "首板启动",
                        "stock_code": r_first.get("stock_code", ""),
                        "stock_name": r_first.get("stock_name", ""),
                        "main_concept": r_first.get("main_concept", ""),
                        "score": float(r_first.get("first_board_score", 0.0)),
                        "reason": f"首板启动信号最强，今日涨幅={r_first.get('ret_today', 0):.2f}%，昨日涨幅={r_first.get('ret_yday', 0):.2f}%，20日涨停数={r_first.get('board_count_20d', 0):.0f}",
                    }
                )
        return pd.DataFrame(rows)

    def generate_main_line_leaders(self):
        self.leader_btn.setEnabled(False)
        try:
            if self._last_rank_df is None or self._last_rank_df.empty or not self._last_recent_pairs:
                self.refresh_group_result()
            if self._last_rank_df is None or self._last_rank_df.empty or not self._last_recent_pairs:
                return

            latest_d8 = self._last_recent_pairs[-1][0]
            cand, daily_file_name = self._build_candidate_dataframe(self._last_rank_df, latest_d8)
            if cand is None or cand.empty:
                self.status_label.setText(f"核心标的生成失败：{daily_file_name}")
                QMessageBox.warning(self, "核心标的生成失败", str(daily_file_name))
                return

            top_main = self._last_rank_df.head(3)["concept"].astype(str).tolist()
            role_df = self._pick_role_rows_by_concept(cand, top_main)
            if role_df.empty:
                self.status_label.setText("核心标的生成失败：未选出有效标的")
                return

            out_name = f"main_line_leaders_{latest_d8[:4]}-{latest_d8[4:6]}-{latest_d8[6:8]}.xlsx"
            out_fp = os.path.join(self._history_dir, out_name)
            with pd.ExcelWriter(out_fp, engine="openpyxl") as writer:
                role_df.to_excel(writer, sheet_name="leaders", index=False)
                cand.to_excel(writer, sheet_name="candidates", index=False)

            leader_detail_lines: List[str] = []
            for concept in top_main:
                if not concept:
                    continue
                leader_detail_lines.append(f"[{concept}]")
                one = role_df[role_df["concept_group"] == concept]
                if one.empty:
                    leader_detail_lines.append("  无可用候选标的")
                    leader_detail_lines.append("")
                    continue
                for _, r in one.iterrows():
                    leader_detail_lines.append(
                        f"{r['role']}: {self._to_voice_stock_code(r['stock_code'])} {r['stock_name']}"
                    )
                leader_detail_lines.append("")

            ui_lines = [
                self.result_edit.toPlainText().strip(),
                "",
                "最强主线核心标的",
                "",
            ] + leader_detail_lines
            final_text = "\n".join(ui_lines).strip()
            self.result_edit.setPlainText(final_text)

            save_lines = (
                self._group_rank_lines(self._last_rank_df)
                + ["", "最强主线核心标的", ""]
                + leader_detail_lines
            )
            save_text = "\n".join(save_lines).strip()
            text_name = f"main_line_leaders_{latest_d8[:4]}-{latest_d8[4:6]}-{latest_d8[6:8]}.txt"
            self._last_leader_text_path = self._save_result_text(text_name, save_text)
            self.status_label.setText(f"核心标的生成完成，已保存：{out_name}；{text_name}")
        except Exception as e:
            self.status_label.setText(f"核心标的生成失败：{e}")
            QMessageBox.warning(self, "核心标的生成失败", str(e))
        finally:
            self.leader_btn.setEnabled(True)

    def run_auto_pipeline(self):
        """自动模式：执行分组与核心标的，保存文本后自动退出。"""
        ok = False
        try:
            self.refresh_group_result()
            self.generate_main_line_leaders()
            ok = bool(self._last_leader_text_path and os.path.exists(self._last_leader_text_path))
            if ok:
                self.status_label.setText(f"自动运行完成，已保存文本：{os.path.basename(self._last_leader_text_path)}，即将退出")
            else:
                self.status_label.setText("自动运行结束：未检测到核心标的文本文件，仍将退出")
        except Exception as e:
            self.status_label.setText(f"自动运行失败：{e}，即将退出")
        finally:
            # 稍作停留，确保状态文字可见并完成 UI 刷新，然后退出
            QTimer.singleShot(10000, self.accept)


def main():
    parser = argparse.ArgumentParser(description="主线分组（独立版）")
    parser.add_argument(
        "--auto-run",
        action="store_true",
        help="启动后自动执行“主线分组+核心标的”，保存文本后自动退出",
    )
    args, _unknown = parser.parse_known_args()

    app = QApplication(sys.argv)
    dlg = MainLineGroupDialog(auto_run=bool(args.auto_run))
    dlg.show()
    if args.auto_run:
        QTimer.singleShot(200, dlg.run_auto_pipeline)
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
