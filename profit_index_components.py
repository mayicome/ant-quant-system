#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
赚钱指数组件（可被多个 GUI 复用）

目标：
- 避免 `profit_index_gui.py` 复用整个 `auto_limit_up_filter.py`（减少副作用/无关导入）
- 只提供：
  1) ProfitIndexCalculatorThread：赚钱指数计算线程
  2) ProfitIndexChartWidget：图表展示组件
"""

import logging
import os
import warnings
import json
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QVBoxLayout, QWidget, QLabel

logger = logging.getLogger(__name__)

# matplotlib（在 GUI 场景下使用 Qt5Agg）
import matplotlib

matplotlib.use("Qt5Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

import numpy as np
import pandas as pd

from utils.daily_cache_reader import (
    get_cache_dir,
    load_daily_from_cache,
    to_full_stock_code,
)
from utils.trading_day import get_trading_dates, is_tradeday


class ProfitIndexCalculatorThread(QThread):
    """赚钱指数计算线程"""

    progress_updated = pyqtSignal(int, int, str)  # 当前进度, 总数, 当前股票
    calculation_finished = pyqtSignal(dict)  # 计算结果
    error_occurred = pyqtSignal(str)  # 错误信息

    def __init__(self, days: int = 30, parent=None, stock_limit: Optional[int] = None):
        super().__init__(parent)
        self.days = days  # 要显示的天数
        self.extra_days = 10  # 额外获取的天数（用于计算10日均线）
        self.stock_limit = stock_limit  # 仅用于测试：限制计算的股票数量
        self.is_running = True

    def stop(self):
        self.is_running = False

    @staticmethod
    def _normalize_stock_code(code: object) -> str:
        """
        统一股票代码格式为 6 位数字，兼容：
        - 600000
        - 600000.SH / SH600000
        - SZ000001 / 000001.SZ
        """
        s = str(code or "").strip().upper()
        if not s:
            return ""
        m = re.search(r"(\d{6})", s)
        return m.group(1) if m else s

    def _daily_change_export_path(self, trade_date: str) -> str:
        """按交易日生成涨幅导出文件路径：history_data/daily_change_YYYY-MM-DD.xlsx"""
        root = os.path.dirname(os.path.abspath(__file__))
        history_dir = os.path.join(root, "history_data")
        os.makedirs(history_dir, exist_ok=True)
        ds = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
        return os.path.join(history_dir, f"daily_change_{ds}.xlsx")

    def _daily_change_clean_export_path(self, trade_date: str) -> str:
        """按交易日生成纯净版导出路径：history_data/daily_change_clean_YYYY-MM-DD.xlsx"""
        root = os.path.dirname(os.path.abspath(__file__))
        history_dir = os.path.join(root, "history_data")
        os.makedirs(history_dir, exist_ok=True)
        ds = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
        return os.path.join(history_dir, f"daily_change_clean_{ds}.xlsx")

    def _history_dir(self) -> str:
        root = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(root, "history_data")

    def _concept_summary_export_path(self, trade_date: str) -> str:
        """写入路径：history_data/concept/concept_summary_YYYY-MM-DD.xlsx"""
        from utils.concept_path import concept_summary_path, ensure_concept_data_dir

        hist = self._history_dir()
        ensure_concept_data_dir(hist)
        return concept_summary_path(trade_date, hist)

    def _concept_rank_export_path(self, trade_date: str) -> str:
        """写入路径：history_data/concept/concept_rank_YYYY-MM-DD.xlsx"""
        from utils.concept_path import concept_rank_path, ensure_concept_data_dir

        hist = self._history_dir()
        ensure_concept_data_dir(hist)
        return concept_rank_path(trade_date, hist)

    def _resolve_concept_summary_path(self, trade_date: str) -> Optional[str]:
        from utils.concept_path import resolve_concept_summary_path

        return resolve_concept_summary_path(trade_date, self._history_dir())

    def _resolve_concept_rank_path(self, trade_date: str) -> Optional[str]:
        from utils.concept_path import resolve_concept_rank_path

        return resolve_concept_rank_path(trade_date, self._history_dir())

    def _load_stock_name_map(self) -> Dict[str, str]:
        """读取 data/all_a_stock_info.json，构建 code->name 映射。"""
        try:
            root = os.path.dirname(os.path.abspath(__file__))
            fp = os.path.join(root, "data", "all_a_stock_info.json")
            if not os.path.exists(fp):
                return {}
            with open(fp, "r", encoding="utf-8") as f:
                obj = json.load(f) or {}
            if not isinstance(obj, dict):
                return {}
            m: Dict[str, str] = {}
            for k, v in obj.items():
                if not isinstance(v, dict):
                    continue
                code = str(v.get("stock_code") or k or "").strip()
                name = str(v.get("name") or "").strip()
                norm_code = self._normalize_stock_code(code)
                if norm_code:
                    m[norm_code] = name
            return m
        except Exception:
            return {}

    def _load_concept_stock_pool(self, min_n: int = 15, max_n: int = 120) -> Dict[str, set]:
        """
        从 all_a_stock_info.json 反算“概念 -> 股票代码集合”，并过滤个股数区间。
        """
        try:
            root = os.path.dirname(os.path.abspath(__file__))
            fp = os.path.join(root, "data", "all_a_stock_info.json")
            if not os.path.exists(fp):
                return {}
            with open(fp, "r", encoding="utf-8") as f:
                obj = json.load(f) or {}
            if not isinstance(obj, dict):
                return {}

            concept_map: Dict[str, set] = {}
            for k, v in obj.items():
                if not isinstance(v, dict):
                    continue
                code = str(v.get("stock_code") or k or "").strip()
                concepts = v.get("concepts") or []
                if (not code) or (not isinstance(concepts, list)):
                    continue
                norm_code = self._normalize_stock_code(code)
                if not norm_code:
                    continue
                for c in concepts:
                    concept = str(c or "").strip()
                    if not concept:
                        continue
                    concept_map.setdefault(concept, set()).add(norm_code)

            filtered = {
                concept: codes
                for concept, codes in concept_map.items()
                if min_n <= len(codes) <= max_n
            }
            return filtered
        except Exception:
            return {}

    def _build_five_day_concept_rank(self, trading_dates: List[str], latest_trade_date: str) -> Optional[pd.DataFrame]:
        """
        读取最近5个交易日概念汇总，计算五日综合得分并返回排名表。
        """
        if not trading_dates:
            return None
        recent_5 = trading_dates[-5:] if len(trading_dates) >= 5 else trading_dates[:]
        frames: List[pd.DataFrame] = []
        for d in recent_5:
            fp = self._resolve_concept_summary_path(d)
            if not fp:
                continue
            try:
                df = pd.read_excel(fp)
                if df is None or df.empty or ("concept" not in df.columns):
                    continue
                df = df[["concept", "avg_change_pct", "limit_up_density", "up_ratio"]].copy()
                df["trade_date"] = d
                frames.append(df)
            except Exception:
                continue

        if not frames:
            return None

        all_df = pd.concat(frames, ignore_index=True)
        for c in ["avg_change_pct", "limit_up_density", "up_ratio"]:
            all_df[c] = pd.to_numeric(all_df[c], errors="coerce").fillna(0.0)

        grp = all_df.groupby("concept", as_index=False).agg(
            five_day_sum_chg=("avg_change_pct", "sum"),
            five_day_avg_zt=("limit_up_density", "mean"),
            five_day_avg_up=("up_ratio", "mean"),
            day_count=("trade_date", "nunique"),
            single_day_max_chg=("avg_change_pct", "max"),
        )
        today_df = (
            all_df[all_df["trade_date"] == latest_trade_date][["concept", "avg_change_pct"]]
            .rename(columns={"avg_change_pct": "today_avg_change_pct"})
            .drop_duplicates(subset=["concept"])
        )
        grp = grp.merge(today_df, on="concept", how="left")
        grp["today_avg_change_pct"] = pd.to_numeric(
            grp["today_avg_change_pct"], errors="coerce"
        ).fillna(0.0)

        grp["composite_score"] = (
            grp["five_day_sum_chg"] * 0.40
            + grp["five_day_avg_zt"] * 0.35
            + grp["five_day_avg_up"] * 0.25
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
        grp["as_of_date"] = f"{latest_trade_date[:4]}-{latest_trade_date[4:6]}-{latest_trade_date[6:8]}"
        return grp

    def _print_rank_groups(self, rank_df: pd.DataFrame) -> None:
        """打印主线/次主线/轮动分组。"""
        if rank_df is None or rank_df.empty or ("concept" not in rank_df.columns):
            print("分组输出跳过：概念排名为空")
            return
        top_main = rank_df.head(3)["concept"].tolist()
        top_sub = rank_df.iloc[3:6]["concept"].tolist()
        main_pool = set(top_main + top_sub)
        one_day = []
        if "is_one_day_rotation" in rank_df.columns:
            one_day = [
                c for c in rank_df[rank_df["is_one_day_rotation"]]["concept"].tolist()
                if c not in main_pool
            ]
        main_line = f"最强主线 TOP1-3: {', '.join(top_main) if top_main else '无'}"
        sub_line = f"次主线 TOP4-6: {', '.join(top_sub) if top_sub else '无'}"
        rot_line = f"一日游或新主线: {', '.join(one_day) if one_day else '无'}"
        logger.info(main_line)
        logger.info(sub_line)
        logger.info(rot_line)
        print(main_line)
        print(sub_line)
        print(rot_line)

    def _load_universe_codes(self) -> List[str]:
        """股票池：优先 a_share_universe.json，否则扫 daily_cache 目录。"""
        root = os.path.dirname(os.path.abspath(__file__))
        uni_path = os.path.join(root, "data", "a_share_universe.json")
        codes: List[str] = []
        if os.path.isfile(uni_path):
            try:
                with open(uni_path, "r", encoding="utf-8") as f:
                    obj = json.load(f) or {}
                raw = obj.get("codes") if isinstance(obj, dict) else None
                if isinstance(raw, list):
                    codes = [to_full_stock_code(c) for c in raw if str(c).strip()]
            except Exception as e:
                logger.warning("读取 a_share_universe.json 失败: %s", e)

        if not codes:
            cache_dir = get_cache_dir()
            if os.path.isdir(cache_dir):
                for name in os.listdir(cache_dir):
                    if name.endswith(".csv") and "." in name[:-4]:
                        codes.append(name[:-4])

        # 去重保序
        seen = set()
        out: List[str] = []
        for c in codes:
            full = to_full_stock_code(c)
            if full and full not in seen:
                seen.add(full)
                out.append(full)
        return out

    @staticmethod
    def _amount_from_bar(close: float, volume: float, amount: object = None) -> Optional[float]:
        """优先用缓存真实 amount；没有则按 收盘价×手数×100 估算。"""
        try:
            if amount is not None and str(amount).strip() != "":
                amt = float(amount)
                if amt > 0:
                    return amt
        except Exception:
            pass
        try:
            c = float(close)
            v = float(volume)
        except Exception:
            return None
        if not (c > 0 and v > 0):
            return None
        return c * v * 100.0

    def _get_trading_dates(self) -> List[str]:
        """获取最近 N+extra+1 个交易日（返回升序 YYYYMMDD）。"""
        total_days_needed = self.days + self.extra_days + 1
        try:
            dates = get_trading_dates(total_days_needed, None)
            if dates and len(dates) >= 2:
                return [d.strftime("%Y%m%d") for d in dates]
        except Exception as e:
            logger.warning("get_trading_dates 失败: %s，回退逐日判断", e)

        current_date = datetime.now()
        if current_date.hour < 15:
            current_date = current_date - timedelta(days=1)
        trading_dates: List[str] = []
        check_date = current_date
        while len(trading_dates) < total_days_needed:
            d = check_date.date() if isinstance(check_date, datetime) else check_date
            if is_tradeday(d):
                trading_dates.append(d.strftime("%Y%m%d"))
            check_date = check_date - timedelta(days=1)
            if (current_date - check_date).days > 150:
                break
        trading_dates.reverse()
        return trading_dates

    def run(self):
        """运行计算（日线只读 data/daily_cache，不再逐只 xtdata 下载）。"""
        try:
            all_stocks = self._load_universe_codes()
            if not all_stocks:
                self.error_occurred.emit(
                    "股票池为空：请确认 data/a_share_universe.json 或 data/daily_cache 已就绪。"
                )
                return

            if self.stock_limit is not None:
                try:
                    limit_n = int(self.stock_limit)
                except Exception:
                    limit_n = 0
                if limit_n > 0:
                    all_stocks = all_stocks[:limit_n]

            logger.info(f"赚钱指数计算：共 {len(all_stocks)} 只股票（daily_cache）")

            trading_dates = self._get_trading_dates()
            if len(trading_dates) < 2:
                self.error_occurred.emit("交易日数据不足")
                return

            logger.info(f"赚钱指数计算：交易日范围 {trading_dates[0]} ~ {trading_dates[-1]}")

            daily_stats = {d: {"up": 0, "down": 0, "flat": 0, "amount": 0.0} for d in trading_dates[1:]}
            latest_trade_date = trading_dates[-1]
            end_as_date = datetime.strptime(latest_trade_date, "%Y%m%d").date()
            start_as_date = datetime.strptime(trading_dates[0], "%Y%m%d").date()

            now_dt = datetime.now()
            export_after_close = now_dt.hour >= 15
            export_path = self._daily_change_export_path(latest_trade_date)
            clean_export_path = self._daily_change_clean_export_path(latest_trade_date)
            concept_export_path = self._concept_summary_export_path(latest_trade_date)
            concept_rank_export_path = self._concept_rank_export_path(latest_trade_date)
            should_export_daily_change = export_after_close and (not os.path.exists(export_path))
            need_clean_export = export_after_close and (not os.path.exists(clean_export_path))
            # 新/旧目录任一已有则跳过重写；写出一律落 concept 子目录
            need_concept_export = export_after_close and (
                self._resolve_concept_summary_path(latest_trade_date) is None
            )
            need_rank_export = export_after_close and (
                self._resolve_concept_rank_path(latest_trade_date) is None
            )
            daily_change_rows: List[Dict[str, object]] = []

            total = len(all_stocks)
            logger.info(f"开始计算赚钱指数（共 {total} 只股票，读 daily_cache）...")

            for idx, stock_code in enumerate(all_stocks):
                if not self.is_running:
                    break

                if idx % 100 == 0:
                    self.progress_updated.emit(idx + 1, total, str(stock_code))

                try:
                    stock_df = load_daily_from_cache(stock_code, through_date=end_as_date)
                    if stock_df is None or stock_df.empty or "close" not in stock_df.columns:
                        continue

                    # 截到需要的交易日起点，减少循环
                    stock_df = stock_df[stock_df["date"] >= start_as_date]
                    if stock_df.empty:
                        continue

                    by_day: Dict[str, Dict[str, float]] = {}
                    for _, row in stock_df.iterrows():
                        d = row["date"]
                        if isinstance(d, datetime):
                            d = d.date()
                        if not isinstance(d, date):
                            continue
                        ds = d.strftime("%Y%m%d")
                        try:
                            close_v = float(row["close"])
                        except Exception:
                            continue
                        if not (close_v > 0):
                            continue
                        vol_v = 0.0
                        try:
                            vol_v = float(row["volume"]) if "volume" in stock_df.columns else 0.0
                        except Exception:
                            vol_v = 0.0
                        by_day[ds] = {
                            "close": close_v,
                            "volume": vol_v,
                            "amount": row["amount"] if "amount" in stock_df.columns else None,
                        }

                    prev_close = None
                    for trade_date in trading_dates:
                        bar = by_day.get(trade_date)
                        if bar is None:
                            prev_close = None
                            continue
                        close = bar["close"]
                        if prev_close is not None and trade_date in daily_stats:
                            if close > prev_close:
                                daily_stats[trade_date]["up"] += 1
                            elif close < prev_close:
                                daily_stats[trade_date]["down"] += 1
                            else:
                                daily_stats[trade_date]["flat"] += 1

                            amt_v = self._amount_from_bar(close, bar["volume"], bar.get("amount"))
                            if amt_v is not None:
                                daily_stats[trade_date]["amount"] += amt_v

                            if should_export_daily_change and trade_date == latest_trade_date:
                                try:
                                    prev_v = float(prev_close)
                                    close_v = float(close)
                                    if prev_v != 0:
                                        pct = (close_v - prev_v) / prev_v * 100.0
                                        amount_yi_v = (amt_v / 1e8) if amt_v is not None else None
                                        daily_change_rows.append(
                                            {
                                                "date": f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}",
                                                "stock_code": self._normalize_stock_code(stock_code),
                                                "prev_close": prev_v,
                                                "close": close_v,
                                                "change_pct": float(pct),
                                                "amount": amt_v,
                                                "amount_yi": amount_yi_v,
                                            }
                                        )
                                except Exception:
                                    pass
                        prev_close = close
                except Exception:
                    continue

            # 计算所有天数的赚钱指数
            all_dates: List[str] = []
            all_profit_index: List[float] = []
            all_up_count: List[int] = []
            all_down_count: List[int] = []
            all_flat_count: List[int] = []
            all_turnover_amount_yi: List[float] = []

            for trade_date in trading_dates[1:]:
                stats = daily_stats[trade_date]
                up = stats["up"]
                down = stats["down"]
                flat = stats["flat"]
                total_ud = up + down + flat

                if total_ud > 0:
                    profit_idx = (up - down) / total_ud * 100
                else:
                    profit_idx = 0

                all_dates.append(trade_date)
                all_profit_index.append(float(profit_idx))
                all_up_count.append(int(up))
                all_down_count.append(int(down))
                all_flat_count.append(int(flat))
                all_turnover_amount_yi.append(float(stats.get("amount", 0.0) / 1e8))

            # 计算5日均线和10日均线
            all_ma5: List[Optional[float]] = []
            all_ma10: List[Optional[float]] = []
            for i in range(len(all_profit_index)):
                if i < 4:
                    all_ma5.append(None)
                else:
                    ma5 = sum(all_profit_index[i - 4 : i + 1]) / 5
                    all_ma5.append(ma5)

                if i < 9:
                    all_ma10.append(None)
                else:
                    ma10 = sum(all_profit_index[i - 9 : i + 1]) / 10
                    all_ma10.append(ma10)

            # 只返回最后 days 天的数据
            display_count = min(self.days, len(all_dates))
            result = {
                "dates": all_dates[-display_count:],
                "profit_index": all_profit_index[-display_count:],
                "up_count": all_up_count[-display_count:],
                "down_count": all_down_count[-display_count:],
                "flat_count": all_flat_count[-display_count:],
                "turnover_amount_yi": all_turnover_amount_yi[-display_count:],
                "ma5": all_ma5[-display_count:],
                "ma10": all_ma10[-display_count:],
            }

            if should_export_daily_change or need_clean_export or need_concept_export or need_rank_export:
                try:
                    export_df = None
                    if should_export_daily_change:
                        if daily_change_rows:
                            export_df = pd.DataFrame(daily_change_rows)
                            export_df = export_df.sort_values("change_pct", ascending=False).reset_index(drop=True)
                            name_map = self._load_stock_name_map()
                            if "stock_code" in export_df.columns:
                                export_df["stock_name"] = export_df["stock_code"].map(lambda x: name_map.get(str(x), ""))
                            export_df.to_excel(export_path, index=False)
                            logger.info(f"已导出当日涨幅文件: {export_path} (共 {len(export_df)} 只股票)")
                        else:
                            logger.info("当日涨幅导出已跳过：无可用涨幅记录")

                    if export_df is None and os.path.exists(export_path):
                        export_df = pd.read_excel(export_path)

                    clean_df = None
                    if export_df is not None and (should_export_daily_change or need_clean_export):
                        clean_df = export_df.copy()
                        if "stock_name" in clean_df.columns:
                            nm = clean_df["stock_name"].fillna("").astype(str).str.upper()
                            is_st = nm.str.contains("ST", regex=False)
                            clean_df = clean_df[~is_st]
                        if "amount" in clean_df.columns:
                            amt = pd.to_numeric(clean_df["amount"], errors="coerce").fillna(0.0)
                            clean_df = clean_df[amt >= 3000000]
                        clean_df = clean_df.reset_index(drop=True)
                        if should_export_daily_change or need_clean_export:
                            clean_df.to_excel(clean_export_path, index=False)
                            logger.info(f"已导出纯净版涨幅文件: {clean_export_path} (共 {len(clean_df)} 只股票)")

                    if clean_df is None and os.path.exists(clean_export_path):
                        clean_df = pd.read_excel(clean_export_path)

                    if (clean_df is not None) and (should_export_daily_change or need_concept_export):
                        concept_pool = self._load_concept_stock_pool(min_n=15, max_n=120)
                        if concept_pool:
                            clean_df["stock_code"] = clean_df["stock_code"].map(self._normalize_stock_code)
                            code_to_change = dict(
                                zip(
                                    clean_df["stock_code"],
                                    pd.to_numeric(clean_df["change_pct"], errors="coerce").fillna(0.0),
                                )
                            )
                            valid_code_set = set(code_to_change.keys())
                            concept_rows: List[Dict[str, object]] = []
                            for concept, concept_codes in concept_pool.items():
                                hit_codes = [c for c in concept_codes if c in valid_code_set]
                                n_valid = len(hit_codes)
                                if n_valid == 0:
                                    concept_rows.append({"concept": concept, "valid_stock_count": 0, "avg_change_pct": None, "limit_up_density": 0.0, "up_ratio": 0.0})
                                    continue
                                changes = [float(code_to_change[c]) for c in hit_codes]
                                concept_rows.append(
                                    {
                                        "concept": concept,
                                        "valid_stock_count": n_valid,
                                        "avg_change_pct": float(sum(changes) / n_valid),
                                        "limit_up_density": float(sum(1 for x in changes if x >= 9.8) / n_valid),
                                        "up_ratio": float(sum(1 for x in changes if x > 0) / n_valid),
                                    }
                                )
                            concept_df = pd.DataFrame(concept_rows).sort_values(
                                ["limit_up_density", "avg_change_pct", "valid_stock_count"],
                                ascending=[False, False, False],
                            ).reset_index(drop=True)
                            concept_df.to_excel(concept_export_path, index=False)
                            logger.info(f"已导出概念汇总文件: {concept_export_path} (共 {len(concept_df)} 个概念)")

                    # 五日概念排名（读取最近5个交易日汇总，包括今天）
                    if should_export_daily_change or need_rank_export or need_concept_export:
                        rank_df = self._build_five_day_concept_rank(trading_dates, latest_trade_date)
                        if rank_df is not None and (not rank_df.empty):
                            rank_df.to_excel(concept_rank_export_path, index=False)
                            logger.info(f"已导出概念五日排名文件: {concept_rank_export_path} (共 {len(rank_df)} 个概念)")
                            self._print_rank_groups(rank_df)

                    # 即使本次未重算排名，也尝试读取已有文件并打印分组，保证每次运行可见
                    existing_rank = self._resolve_concept_rank_path(latest_trade_date)
                    if existing_rank:
                        try:
                            rank_df_existing = pd.read_excel(existing_rank)
                            self._print_rank_groups(rank_df_existing)
                        except Exception as e:
                            print(f"分组输出失败：读取排名文件出错: {e}")
                    else:
                        print("分组输出跳过：当日概念排名文件不存在")
                except Exception as e:
                    logger.warning(f"导出当日涨幅文件失败: {e}")

            logger.info(f"赚钱指数计算完成，共 {len(result['dates'])} 个交易日（含均线数据）")
            self.calculation_finished.emit(result)

        except Exception as e:
            logger.error(f"赚钱指数计算出错: {str(e)}", exc_info=True)
            self.error_occurred.emit(f"计算出错: {str(e)}")


class ProfitIndexChartWidget(QWidget):
    """赚钱指数图表组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        self.figure = Figure(figsize=(10, 6), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas)

        # 初始状态不要显示“正在计算”，避免误导
        self.status_label = QLabel("准备计算赚钱指数...")
        self.status_label.setAlignment(Qt.AlignCenter)
        _sf = QFont(self.status_label.font())
        _psz = _sf.pointSize()
        target_pt = (_psz + 6) if _psz > 0 else 17
        _sf.setPointSize(target_pt)
        self.status_label.setFont(_sf)
        layout.addWidget(self.status_label)

        plt.rcParams["font.sans-serif"] = ["SimHei", "Microsoft YaHei", "Arial Unicode MS"]
        plt.rcParams["axes.unicode_minus"] = False

    def update_chart(self, data: dict):
        """更新图表"""
        self.figure.clear()

        if not data or not data.get("dates"):
            self.status_label.setText("无数据")
            self.canvas.draw()
            return

        dates = data["dates"]
        profit_index = data["profit_index"]
        up_count = data["up_count"]
        down_count = data["down_count"]
        flat_count = data["flat_count"]
        turnover_amount_yi = data.get("turnover_amount_yi", [0.0] * len(dates))
        ma5 = data["ma5"]
        ma10 = data.get("ma10", [None] * len(dates))

        date_labels = [f"{d[4:6]}/{d[6:8]}" for d in dates]
        x = np.arange(len(dates))

        gs = self.figure.add_gridspec(2, 1, height_ratios=[2, 1], hspace=0.1)
        ax1 = self.figure.add_subplot(gs[0])

        ax1.plot(x, profit_index, "o-", color="#333333", linewidth=1.5, markersize=6, label="赚钱指数")

        for i in range(len(x)):
            if profit_index[i] >= 0:
                ax1.fill_between(
                    [x[i] - 0.3, x[i] + 0.3],
                    [0, 0],
                    [profit_index[i], profit_index[i]],
                    color="#FFCCCC",
                    alpha=0.7,
                )
            else:
                ax1.fill_between(
                    [x[i] - 0.3, x[i] + 0.3],
                    [profit_index[i], profit_index[i]],
                    [0, 0],
                    color="#CCFFCC",
                    alpha=0.7,
                )

        ma5_valid = [(i, v) for i, v in enumerate(ma5) if v is not None]
        if ma5_valid:
            ma5_x, ma5_y = zip(*ma5_valid)
            ax1.plot(ma5_x, ma5_y, "--", color="#1565C0", linewidth=2, label="MA5")

        ma10_valid = [(i, v) for i, v in enumerate(ma10) if v is not None]
        if ma10_valid:
            ma10_x, ma10_y = zip(*ma10_valid)
            ax1.plot(ma10_x, ma10_y, "--", color="#7B1FA2", linewidth=2, label="MA10")

        ax1.axhline(y=0, color="#666666", linestyle="--", linewidth=1.5, label="平衡线(0)")
        ax1.set_title("A股赚钱指数（最近30个交易日）", fontsize=18, fontweight="bold")
        ax1.set_ylabel("赚钱指数", fontsize=10)
        ax1.legend(loc="upper left", fontsize=8)
        ax1.grid(True, alpha=0.3)
        ax1.set_xticks(x)
        ax1.set_xticklabels([])

        y_abs_max = max(abs(min(profit_index)), abs(max(profit_index)), 10) * 1.2
        ax1.set_ylim(-y_abs_max, y_abs_max)

        _node_label_fs = 12
        for i, v in enumerate(profit_index):
            color = "#FF4444" if v >= 0 else "#00AA00"
            offset_y = 10 if v >= 0 else -14
            ax1.annotate(
                f"{v:.0f}",
                (x[i], v),
                textcoords="offset points",
                xytext=(0, offset_y),
                ha="center",
                fontsize=_node_label_fs,
                color=color,
                fontweight="bold",
            )

        ax2 = self.figure.add_subplot(gs[1])
        bar_width = 0.6
        ax2.bar(x, up_count, bar_width, label="上涨", color="#FF4444", alpha=1.0, edgecolor="none")
        ax2.bar(x, flat_count, bar_width, bottom=up_count, label="平盘", color="#616161", alpha=1.0, edgecolor="none")
        ax2.bar(
            x,
            down_count,
            bar_width,
            bottom=[u + f for u, f in zip(up_count, flat_count)],
            label="下跌",
            color="#00AA00",
            alpha=1.0,
            edgecolor="none",
        )

        ax2.set_ylabel("家数", fontsize=10)
        ax2.set_xticks(x)
        ax2.set_xticklabels(date_labels, rotation=45, ha="right", fontsize=8)
        ax2_t = ax2.twinx()
        ax2_t.plot(
            x,
            turnover_amount_yi,
            color="#1565C0",
            linewidth=1.8,
            marker="o",
            markersize=3.5,
            alpha=0.9,
            label="成交额(亿元)",
        )
        ax2_t.set_ylabel("成交额(亿元)", fontsize=10, color="#1565C0")
        ax2_t.tick_params(axis="y", labelcolor="#1565C0")
        if turnover_amount_yi:
            max_amt = max(float(v or 0.0) for v in turnover_amount_yi)
            ax2_t.set_ylim(0, max(1.0, max_amt * 1.15))
        h1, l1 = ax2.get_legend_handles_labels()
        h2, l2 = ax2_t.get_legend_handles_labels()
        ax2.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8, ncol=4)
        ax2.grid(True, alpha=0.3, axis="y")

        # 屏蔽 matplotlib 的 tight_layout 兼容性 UserWarning
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="This figure includes Axes that are not compatible with tight_layout*",
                category=UserWarning,
            )
            self.figure.tight_layout()
        self.canvas.draw()

        latest_idx = profit_index[-1]
        # 1~3 个笑脸/哭脸表示难易程度
        if latest_idx > 70:
            status = "赚钱特别容易 " + "😊" * 3
        elif latest_idx > 20:
            status = "赚钱较容易 " + "😊" * 2
        elif latest_idx > 0:
            status = "赚钱容易 " + "😊"
        elif latest_idx > -20:
            status = "赚钱困难 " + "😭"
        elif latest_idx <= -70:
            status = "赚钱特别难 " + "😭" * 3
        else:
            status = "赚钱很困难 " + "😭" * 2

        latest_amt_yi = float(turnover_amount_yi[-1]) if turnover_amount_yi else 0.0
        self.status_label.setText(f"最新赚钱难易度: {latest_idx:.1f} ({status}) | 当日成交额: {latest_amt_yi:.0f}亿")

