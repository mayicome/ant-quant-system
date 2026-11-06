#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
推荐值计算服务
一次一股：经 QMT 探针内存算买+卖，ANTRES rec 回传；本进程不读 tick、不外传 tick。
"""

import threading
import queue
from datetime import date, datetime, timedelta
from utils.logger import Logger


class RecommendationService:
    """推荐值计算服务（探针 RPC）"""

    def __init__(self, max_concurrent_tasks=1):
        """
        Args:
            max_concurrent_tasks: 外层并发排队上限（探针侧 max_items=1 + _BUSY）
        """
        self.logger = Logger()
        self.recommendations = {}
        self._calculating = set()
        self._lock = threading.Lock()
        self.max_concurrent_tasks = max(1, int(max_concurrent_tasks or 1))
        self._active_tasks = 0
        self._task_queue = queue.Queue()
        self._worker_thread = None
        self._stop_worker = False
        self._start_worker()

    def get_sell_recommendations(self, stock_code):
        with self._lock:
            if stock_code in self.recommendations:
                sell_data = self.recommendations[stock_code].get("sell")
                if sell_data:
                    return sell_data
        return None

    def get_buy_recommendations(self, stock_code):
        with self._lock:
            if stock_code in self.recommendations:
                buy_data = self.recommendations[stock_code].get("buy")
                if buy_data:
                    return buy_data
        return None

    def _start_worker(self):
        if self._worker_thread is None or not self._worker_thread.is_alive():
            self._stop_worker = False
            self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self._worker_thread.start()

    def _worker_loop(self):
        while not self._stop_worker:
            try:
                task = self._task_queue.get(timeout=1)
                if task is None:
                    break
                _task_type, stock_code, days, sample_interval, callback = task

                with self._lock:
                    if stock_code in self._calculating:
                        self.logger.debug("股票 %s 的推荐值正在计算中，跳过" % stock_code)
                        self._task_queue.task_done()
                        continue
                    self._calculating.add(stock_code)
                    self._active_tasks += 1

                try:
                    # 合并同股后续 buy/sell 任务（一次 RPC 同时出买+卖）
                    pending_callbacks = []
                    if callback:
                        pending_callbacks.append(callback)
                    while True:
                        try:
                            next_task = self._task_queue.get_nowait()
                            if next_task[1] == stock_code:
                                if next_task[4]:
                                    pending_callbacks.append(next_task[4])
                                # 用最新的 days / sample_interval
                                days = next_task[2] or days
                                sample_interval = next_task[3] or sample_interval
                                self._task_queue.task_done()
                            else:
                                self._task_queue.put(next_task)
                                break
                        except queue.Empty:
                            break

                    self._calculate_via_rpc(
                        stock_code,
                        days=days,
                        sample_interval=sample_interval,
                        callbacks=pending_callbacks,
                    )
                finally:
                    with self._lock:
                        self._calculating.discard(stock_code)
                        self._active_tasks -= 1
                self._task_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                self.logger.error("工作线程处理任务失败: %s" % e, exc_info=True)

    def _recent_trading_days(self, stock_code, days):
        try:
            from best_sell_drop_percent_calculator import BestSellDropPercentCalculator

            calc = BestSellDropPercentCalculator(stock_code)
            return calc.get_recent_trading_days(count=int(days or 3))
        except Exception as e:
            self.logger.error("获取交易日失败 %s: %s" % (stock_code, e), exc_info=True)
            return []

    def _poll_formula(self):
        try:
            from utils import qmt_formula_result_log as fl

            fl.poll()
        except Exception:
            pass

    def _calculate_via_rpc(self, stock_code, days=3, sample_interval=30, callbacks=None):
        """一次一股经探针算买+卖；失败时 UI 可见 error。"""
        callbacks = callbacks or []
        try:
            trading_days = self._recent_trading_days(stock_code, days)
            if not trading_days:
                self._mark_error(stock_code, "no_trading_days")
                return

            day_strs = []
            for d in trading_days:
                if hasattr(d, "strftime"):
                    day_strs.append(d.strftime("%Y%m%d"))
                else:
                    day_strs.append(str(d).replace("-", "")[:8])

            from utils.recommendation_request import request_rec

            payload = request_rec(
                stock_code,
                day_strs,
                sample_interval=int(sample_interval or 30),
                timeout_sec=180.0,
                poll_fn=self._poll_formula,
            )
            if not payload:
                self._mark_error(stock_code, "timeout_or_no_ack")
                self._notify(callbacks, stock_code)
                return

            st = str(payload.get("st") or "")
            if st != "ok":
                msg = str(payload.get("msg") or st or "fail")
                self._mark_error(stock_code, msg)
                self._notify(callbacks, stock_code)
                return

            last_update_time = datetime.now()
            sell = {
                "strategy1": float(payload.get("s1") or 0),
                "strategy1_sell_rate": float(payload.get("s1r") or 0),
                "strategy1_avg_profit": float(payload.get("s1p") or 0),
                "strategy1_max_drawdown": float(payload.get("s1d") or 0),
                "strategy2": float(payload.get("s2") or 0),
                "strategy2_sell_rate": float(payload.get("s2r") or 0),
                "strategy2_avg_profit": float(payload.get("s2p") or 0),
                "strategy2_max_drawdown": float(payload.get("s2d") or 0),
                "last_update": last_update_time,
                "status": "calculated",
                "source": "qmt_rec",
                "nd": payload.get("nd"),
                "n": payload.get("n"),
            }
            buy = {
                "strategy1": float(payload.get("b1") or 0),
                "strategy1_buy_rate": float(payload.get("b1r") or 0),
                "strategy1_avg_saving": float(payload.get("b1s") or 0),
                "strategy1_max_drawdown": float(payload.get("b1d") or 0),
                "strategy2": float(payload.get("b2") or 0),
                "strategy2_buy_rate": float(payload.get("b2r") or 0),
                "strategy2_avg_saving": float(payload.get("b2s") or 0),
                "strategy2_max_drawdown": float(payload.get("b2d") or 0),
                "last_update": last_update_time,
                "status": "calculated",
                "source": "qmt_rec",
                "nd": payload.get("nd"),
                "n": payload.get("n"),
            }
            with self._lock:
                if stock_code not in self.recommendations:
                    self.recommendations[stock_code] = {}
                self.recommendations[stock_code]["sell"] = sell
                self.recommendations[stock_code]["buy"] = buy
            self._notify(callbacks, stock_code)
        except Exception as e:
            self.logger.error("计算 %s 推荐值失败: %s" % (stock_code, e), exc_info=True)
            self._mark_error(stock_code, str(e))
            self._notify(callbacks, stock_code)

    def _notify(self, callbacks, stock_code):
        with self._lock:
            sell = (self.recommendations.get(stock_code) or {}).get("sell")
            buy = (self.recommendations.get(stock_code) or {}).get("buy")
        for cb in callbacks or []:
            try:
                # 兼容旧回调签名 callback(code, rec)；传合并包里当前有的一侧
                if sell:
                    cb(stock_code, sell)
                elif buy:
                    cb(stock_code, buy)
            except Exception:
                pass

    def _mark_error(self, stock_code, msg):
        err = {"status": "error", "error": str(msg or "error")[:200], "last_update": datetime.now()}
        with self._lock:
            if stock_code not in self.recommendations:
                self.recommendations[stock_code] = {}
            self.recommendations[stock_code]["sell"] = dict(err)
            self.recommendations[stock_code]["buy"] = dict(err)

    def calculate_sell_recommendations_async(self, stock_code, days=3, sample_interval=30, callback=None):
        try:
            self._task_queue.put(("sell", stock_code, days, sample_interval, callback), block=False)
        except queue.Full:
            self.logger.warning("任务队列已满，跳过 %s 的卖出推荐值计算" % stock_code)
            self._mark_error(stock_code, "queue_full")

    def calculate_buy_recommendations_async(self, stock_code, days=3, sample_interval=30, callback=None):
        try:
            self._task_queue.put(("buy", stock_code, days, sample_interval, callback), block=False)
        except queue.Full:
            self.logger.warning("任务队列已满，跳过 %s 的买入推荐值计算" % stock_code)
            self._mark_error(stock_code, "queue_full")

    def is_calculating(self, stock_code):
        with self._lock:
            return stock_code in self._calculating

    def clear_cache(self, stock_code=None):
        with self._lock:
            if stock_code:
                if stock_code in self.recommendations:
                    del self.recommendations[stock_code]
            else:
                self.recommendations.clear()


_recommendation_service = None


def get_recommendation_service():
    global _recommendation_service
    if _recommendation_service is None:
        _recommendation_service = RecommendationService()
    return _recommendation_service
