# -*- coding: utf-8 -*-
"""从 QMT 内置策略写入的 results.json 轮询现价，驱动状态栏与图表竖线。"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

from PyQt5.QtCore import QObject, QTimer

from core.utils.security_type import SecurityTypeUtil
from utils.ant_rules_io_ext import (
    default_paths,
    load_results_prices,
    load_account_positions_snapshot,
    load_orders_snapshot,
    load_broker_orders_snapshot,
    load_elastic_states_snapshot,
)
from utils.qmt_execution_config import use_builtin_price_feed


class BuiltinPricePoller(QObject):
    """每 1 秒读取 results.json，更新 latest_prices / price_displays 并发射 tick_data_signal。"""

    def __init__(self, task_manager, qmt_adapter=None, main_window=None, parent=None):
        super().__init__(parent)
        self.task_manager = task_manager
        self.qmt_adapter = qmt_adapter
        self.main_window = main_window
        self.logger = getattr(task_manager, "logger", None)
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._rules_path, self._results_path = default_paths(root)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._poll_once)
        self._last_file_mtime = 0.0
        self._last_prices: Dict[str, float] = {}
        self._last_intraday: Dict[str, Dict[str, float]] = {}
        self._last_status_update = 0.0
        self._last_account_emit = 0.0
        self._seen_order_keys: set = set()
        self._order_status_seen: dict = {}
        # 大 QMT 健康：行情/账户滞后早发现（不依赖打开 QMT 界面）
        self._health_alert = ""
        self._health_last_log_ts = 0.0
        self._health_last_sound_ts = 0.0
        self._health_last_check_ts = 0.0
        self._health_alert_since_ts = 0.0
        self._health_sct_sustain_sec = 120.0  # 持续异常这么久才推 Server酱，避免闪断刷屏

    def start(self) -> None:
        if not use_builtin_price_feed():
            return
        self._timer.start()
        self._poll_once()

    def stop(self) -> None:
        self._timer.stop()

    def _poll_once(self) -> None:
        if not use_builtin_price_feed():
            return
        if not self.task_manager:
            return
        try:
            if not os.path.isfile(self._results_path):
                self._set_health_alert("大QMT无results.json（策略可能未运行）")
                return
            mtime = os.path.getmtime(self._results_path)
            prices = load_results_prices(self._results_path)
            if prices or mtime != self._last_file_mtime:
                self._last_file_mtime = mtime
                self._apply_prices(prices)
            self._apply_account_snapshot()
            self._apply_orders_snapshot()
            self._apply_elastic_states_snapshot()
            self._check_qmt_health(prices, mtime)
        except Exception as e:
            if self.logger:
                self.logger.debug(f"[builtin_price] 轮询失败: {e}")

    def _set_health_alert(self, msg: str) -> None:
        """更新健康告警；变化时刷新状态栏，并节流打日志/音效/Server酱。"""
        msg = str(msg or "").strip()
        prev = str(getattr(self, "_health_alert", "") or "")
        self._health_alert = msg
        # 供状态栏读取
        for holder in (self.qmt_adapter, self.task_manager, self.main_window):
            if holder is not None:
                try:
                    setattr(holder, "builtin_health_alert", msg)
                except Exception:
                    pass

        now = time.time()
        if not msg:
            self._health_alert_since_ts = 0.0
            if msg != prev:
                try:
                    if self.main_window is not None and hasattr(self.main_window, "update_status_bar"):
                        self.main_window.update_status_bar()
                except Exception:
                    pass
            return

        if msg != prev:
            self._health_alert_since_ts = now
            # 告警出现刷状态栏
            try:
                if self.main_window is not None and hasattr(self.main_window, "update_status_bar"):
                    self.main_window.update_status_bar()
            except Exception:
                pass
            if self.logger and now - float(self._health_last_log_ts or 0) >= 60.0:
                self._health_last_log_ts = now
                self.logger.warning(f"[大QMT健康] {msg}")
            if now - float(self._health_last_sound_ts or 0) >= 120.0:
                self._health_last_sound_ts = now
                try:
                    mw = self.main_window
                    if mw is not None and getattr(mw, "sound_enabled", False) and hasattr(mw, "play_trade_sound"):
                        mw.play_trade_sound()
                except Exception:
                    pass
            return

        # 同一告警持续：满 sustain 秒再推微信（闪断不推）
        self._maybe_push_health_server_chan(msg, now)

    def _maybe_push_health_server_chan(self, msg: str, now: Optional[float] = None) -> None:
        now = float(now if now is not None else time.time())
        since = float(getattr(self, "_health_alert_since_ts", 0) or 0)
        need = float(getattr(self, "_health_sct_sustain_sec", 120) or 120)
        if since <= 0 or (now - since) < need:
            return
        try:
            from utils.server_chan_notify import notify_alert

            day = datetime.now().strftime("%Y%m%d")
            r = notify_alert(
                "大QMT交易时段异常",
                "持续约%d秒：%s\n\n请检查大QMT交易/行情连接与「蚂蚁量化规则」是否在跑。"
                % (int(now - since), msg),
                alert_key="qmt_health_intraday_%s" % day,
                cooldown_sec=3600,
            )
            if r.get("success") and self.logger:
                self.logger.warning("[大QMT健康] Server酱已推送")
            elif r.get("skipped"):
                pass
            elif self.logger and not r.get("success"):
                self.logger.debug("[大QMT健康] Server酱失败: %s" % r.get("message"))
        except Exception as e:
            if self.logger:
                self.logger.debug("[大QMT健康] Server酱异常: %s" % e)

    @staticmethod
    def _parse_iso_age_sec(raw, now: Optional[datetime] = None) -> Optional[float]:
        s = str(raw or "").strip()
        if not s:
            return None
        now = now or datetime.now()
        try:
            if "T" in s:
                dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
            elif " " in s:
                dt = datetime.strptime(s[:19], "%Y-%m-%d %H:%M:%S")
            else:
                return None
            return max(0.0, (now - dt).total_seconds())
        except Exception:
            return None

    @staticmethod
    def _tick_time_lag_sec(tick_hhmmss: str, now: Optional[datetime] = None) -> Optional[float]:
        s = str(tick_hhmmss or "").strip()
        if len(s) < 8:
            return None
        now = now or datetime.now()
        try:
            parts = s[:8].split(":")
            t = now.replace(
                hour=int(parts[0]),
                minute=int(parts[1]),
                second=int(parts[2]),
                microsecond=0,
            )
            lag = (now - t).total_seconds()
            # 跨日/异常：忽略过大负值；正滞后正常返回
            if lag < -120:
                return None
            return max(0.0, lag)
        except Exception:
            return None

    def _in_health_watch_window(self, now: Optional[datetime] = None) -> bool:
        """交易日早盘集合竞价 + 连续竞价内监控；关键节点给约 90 秒宽限。"""
        now = now or datetime.now()
        try:
            from utils.trading_day import is_tradeday
        except Exception:
            return False
        if not is_tradeday(now.date()):
            return False
        t = now.time()
        from datetime import time as dt_time

        # 09:15–11:30（含集合竞价）；13:00–15:00
        in_morning = dt_time(9, 15) <= t <= dt_time(11, 30)
        in_afternoon = dt_time(13, 0) <= t <= dt_time(15, 0)
        if not (in_morning or in_afternoon):
            return False
        # 9:15 竞价开始 / 13:00 午后开盘后约 90 秒宽限，避免刚连上误报
        if dt_time(9, 15) <= t < dt_time(9, 16, 30):
            return False
        if dt_time(13, 0) <= t < dt_time(13, 1, 30):
            return False
        return True

    @staticmethod
    def _in_call_auction(now: Optional[datetime] = None) -> bool:
        """早盘集合竞价（含 9:25–9:30 撮合缓冲）。"""
        now = now or datetime.now()
        from datetime import time as dt_time

        t = now.time()
        return dt_time(9, 15) <= t < dt_time(9, 30)

    @staticmethod
    def _in_auction_match_quiet(now: Optional[datetime] = None) -> bool:
        """9:25 撮合后至 9:30 连续竞价前：A 股 tick 时间戳常冻结在约 09:25，墙钟推进会伪报行情滞后。"""
        now = now or datetime.now()
        from datetime import time as dt_time

        t = now.time()
        return dt_time(9, 25) <= t < dt_time(9, 30)

    def _check_qmt_health(self, prices: Dict[str, Dict[str, Any]], file_mtime: float) -> None:
        """根据 results.json 的 tick/账户时间尽早发现大 QMT 卡顿或断连。"""
        now = datetime.now()
        now_ts = time.time()
        # 最多每 2 秒评估一次即可
        if now_ts - float(self._health_last_check_ts or 0) < 2.0:
            return
        self._health_last_check_ts = now_ts

        if not self._in_health_watch_window(now):
            self._set_health_alert("")
            return

        in_auction = self._in_call_auction(now)
        in_match_quiet = self._in_auction_match_quiet(now)

        # 1) 文件/策略心跳
        file_age = max(0.0, now_ts - float(file_mtime or 0))
        results_updated = ""
        if prices:
            for snap in prices.values():
                results_updated = str((snap or {}).get("updated_at") or "")
                if results_updated:
                    break
        results_age = self._parse_iso_age_sec(results_updated, now)
        if results_age is None:
            results_age = file_age

        # 2) 行情推送墙钟：quotes_recv_at（任一 tick/补种刷新），不看 sticky timetag / 价格是否变动
        # 9:25–9:30 撮合静默段：回调可能停；有价则不报推送停（仍报无行情）
        quote_alert = ""
        if not prices:
            # 集合竞价就要有价，否则策略生成会空窗
            quote_alert = "无行情数据"
        elif not in_match_quiet:
            quotes_recv = ""
            for snap in (prices or {}).values():
                if not isinstance(snap, dict):
                    continue
                quotes_recv = str(
                    snap.get("quotes_recv_at") or snap.get("quote_recv_at") or ""
                ).strip()
                if quotes_recv:
                    break
            recv_age = self._parse_iso_age_sec(quotes_recv, now)
            if recv_age is not None:
                # 须晚于 QMT full_tick 补种阈值(约18s)，否则补种前先误报
                lag_thr = 25.0 if in_auction else 50.0
                if recv_age >= lag_thr:
                    quote_alert = "行情推送停约%d秒" % int(recv_age)
                    if in_auction:
                        quote_alert = "集合竞价" + quote_alert
            # 尚无墙钟字段（旧 results / 策略未热更）：不按 timetag 误报

        # 3) 账户快照停滞（交易连接卡但行情可能还在）
        account_alert = ""
        try:
            asset, _positions = load_account_positions_snapshot(self._results_path)
        except Exception:
            asset = None
        if isinstance(asset, dict) and asset:
            acc_age = self._parse_iso_age_sec(asset.get("updated_at"), now)
            # 竞价阶段账户同步也应活着；阈值与连续竞价一致
            if acc_age is not None and acc_age >= 45.0:
                account_alert = "账户快照停约%d秒" % int(acc_age)
        elif results_age is not None and results_age >= 30.0:
            # 无账户块且结果文件也旧
            account_alert = "账户快照缺失"

        # 4) 策略整体停写
        writer_alert = ""
        # 竞价阶段策略也必须持续 flush，否则生成任务取不到价
        writer_thr = 12.0 if in_auction else 15.0
        if results_age is not None and results_age >= writer_thr and file_age >= writer_thr:
            writer_alert = "策略心跳停约%d秒" % int(max(results_age, file_age))
            if in_auction:
                writer_alert = "集合竞价" + writer_alert

        parts = [p for p in (writer_alert, quote_alert, account_alert) if p]
        if not parts:
            self._set_health_alert("")
            return
        self._set_health_alert("⚠ 大QMT " + "；".join(parts) + "（请检查交易/行情连接）")
    def _apply_prices(self, prices: Dict[str, Dict[str, Any]]) -> None:
        tm = self.task_manager
        changed_any = False
        latest_tick_time_str = ""

        for stock_code, snap in prices.items():
            price = float(snap.get("last_price") or 0)
            if price <= 0:
                continue
            tick_time = str(snap.get("last_tick_time") or "").strip()
            if tick_time:
                latest_tick_time_str = tick_time

            open_px = float(snap.get("today_open") or 0)
            hi_px = float(snap.get("today_high") or 0)
            lo_px = float(snap.get("today_low") or 0)
            intraday = {
                "open": open_px,
                "high": hi_px,
                "low": lo_px,
            }

            prev = self._last_prices.get(stock_code)
            price_changed = prev is None or abs(prev - price) > 1e-9
            prev_id = self._last_intraday.get(stock_code) or {}
            intraday_changed = any(
                abs(float(prev_id.get(k) or 0) - float(intraday.get(k) or 0)) > 1e-9
                for k in ("open", "high", "low")
            )

            if price_changed:
                self._last_prices[stock_code] = price
                changed_any = True
            if intraday_changed:
                self._last_intraday[stock_code] = dict(intraday)

            tm.latest_prices[stock_code] = price

            precision = SecurityTypeUtil.get_price_precision(stock_code)
            format_str = f".{precision}f"
            tm.price_displays[stock_code] = f"{price:{format_str}}"

            if (price_changed or intraday_changed) and self.qmt_adapter is not None:
                tick_data = self._build_tick_data(
                    stock_code, price, tick_time, intraday=intraday
                )
                try:
                    self.qmt_adapter.tick_data_signal.emit(tick_data)
                except Exception:
                    pass

        if latest_tick_time_str and self.qmt_adapter is not None:
            sub = getattr(self.qmt_adapter, "subscribe_thread", None)
            if sub is not None:
                sub.latest_tick_time_str = latest_tick_time_str

        if changed_any and self.main_window is not None:
            now = time.time()
            if now - self._last_status_update >= 0.5:
                self._last_status_update = now
                try:
                    if hasattr(self.main_window, "update_status_bar"):
                        self.main_window.update_status_bar()
                except Exception:
                    pass

    def _apply_account_snapshot(self) -> None:
        """大 QMT 内置策略写入 results.json 的资金/持仓 → 主界面。"""
        try:
            asset, positions = load_account_positions_snapshot(self._results_path)
        except Exception:
            return
        if asset is None and not positions:
            return
        adapter = self.qmt_adapter
        if adapter is None:
            return
        if asset:
            adapter.cached_asset = asset
        if isinstance(positions, dict):
            adapter.cached_positions = positions
        now = time.time()
        if now - self._last_account_emit < 2.0:
            return
        self._last_account_emit = now
        try:
            adapter.position_updated.emit(asset or adapter.cached_asset, positions or {})
        except Exception:
            pass

    def _resolve_ui_ext(self):
        """main.py 传入的是 MainWindowExt，不是带 .ext 的 MainWindow。"""
        mw = self.main_window
        if mw is None:
            return None
        if hasattr(mw, "add_trade_record"):
            return mw
        return getattr(mw, "ext", None)

    def _resolve_charts_view(self):
        ext = self._resolve_ui_ext()
        if ext is not None:
            view = getattr(ext, "tasks_charts_view", None)
            if view is not None:
                return view
        mw = self.main_window
        if mw is not None:
            return getattr(mw, "tasks_charts_view", None)
        return None

    # 与大 QMT / XtQuant 一致：委托状态 86=已确认（勿与业务类型 IPO_SUBSCRIBE=86 混淆）
    ORDER_STATUS_TEXT = {
        48: "未报",
        49: "待报",
        50: "已报",
        51: "已报待撤",
        52: "部成待撤",
        53: "部撤",
        54: "已撤",
        55: "部成",
        56: "已成",
        57: "废单",
        86: "已确认",
        255: "未知",
    }

    @staticmethod
    def _trade_type_from_side(side) -> str:
        s = str(side or "buy").strip().lower()
        if s in ("subscribe", "ipo", "申购", "新股申购"):
            return "申购"
        if s in ("sell", "卖出"):
            return "卖出"
        return "买入"

    @staticmethod
    def _is_subscribe_rec(rec: dict) -> bool:
        if not isinstance(rec, dict):
            return False
        side = str(rec.get("side") or "").strip().lower()
        if side in ("subscribe", "ipo", "申购"):
            return True
        opt = str(rec.get("opt_name") or "")
        if "申购" in opt:
            return True
        try:
            if int(rec.get("order_type")) == 86:
                return True
        except (TypeError, ValueError):
            pass
        try:
            if int(rec.get("offset_flag")) == 86:
                return True
        except (TypeError, ValueError):
            pass
        type_text = str(rec.get("order_type_text") or "")
        if "申购" in type_text:
            return True
        return False

    def _apply_orders_snapshot(self) -> None:
        """用大 QMT 真实委托状态刷新订单列表；本地 passorder 记录用于图表回写。"""
        try:
            broker_orders = load_broker_orders_snapshot(self._results_path)
        except Exception:
            broker_orders = []
        try:
            local_orders = load_orders_snapshot(self._results_path)
        except Exception:
            local_orders = []
        self._last_local_orders = local_orders

        try:
            from utils.order_session import filter_order_records

            broker_orders = filter_order_records(broker_orders)
            local_orders = filter_order_records(local_orders)
        except Exception:
            pass

        # 订单列表：只展示大 QMT 柜台委托；本地跳过/硬pass/待查占位仅写执行记录与图表回写
        display = []
        seen = set()
        from core.execution_record_manager import is_unique_broker_sysid

        local_by_sys = {}
        for loc in local_orders or []:
            if not isinstance(loc, dict):
                continue
            sid = str(loc.get("order_sysid") or "").strip()
            if is_unique_broker_sysid(sid):
                local_by_sys[sid] = loc
        for rec in broker_orders or []:
            if not isinstance(rec, dict):
                continue
            sid = str(rec.get("order_sysid") or "").strip()
            if sid and not str(rec.get("order_time") or "").strip() and sid in local_by_sys:
                loc = local_by_sys[sid]
                fill = str(loc.get("at") or loc.get("order_time") or "").strip()
                if fill:
                    rec = dict(rec)
                    rec["order_time"] = fill
                    if not rec.get("at") and loc.get("at"):
                        rec["at"] = loc.get("at")
            key = self._order_identity(rec)
            if key in seen:
                continue
            seen.add(key)
            display.append(rec)

        # 启动后首次 / 换日后：清空订单表再推入，去掉几天前残留行
        try:
            trade_date = ""
            import json
            from pathlib import Path

            p = Path(self._results_path)
            if p.is_file():
                with p.open("r", encoding="utf-8") as f:
                    trade_date = str((json.load(f) or {}).get("trade_date") or "")
            session_key = trade_date or datetime.now().strftime("%Y%m%d")
            need_clear = (
                not getattr(self, "_orders_ui_bootstrapped", False)
                or getattr(self, "_orders_ui_session", None) != session_key
            )
            # 表已被别处清空（如连接阶段误清）时强制再灌入
            if not need_clear:
                try:
                    ext = self._resolve_ui_ext()
                    table = getattr(ext, "tableWidget_3", None) if ext is not None else None
                    if table is not None and table.rowCount() == 0 and display:
                        need_clear = True
                except Exception:
                    pass
            if need_clear:
                self._orders_ui_bootstrapped = True
                self._orders_ui_session = session_key
                self._order_status_seen = {}
                ext = self._resolve_ui_ext()
                if ext is not None and hasattr(ext, "tableWidget_3") and ext.tableWidget_3:
                    ext.tableWidget_3.setRowCount(0)
        except Exception:
            pass

        for rec in display:
            if not isinstance(rec, dict):
                continue
            try:
                self._push_or_refresh_order(rec)
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"[builtin_price] 应用订单失败: {e}")

        # 柜台合同号已入表后，清掉仍残留的 PO「待查」占位行（同一笔）
        try:
            self._sweep_orphan_po_rows(display)
        except Exception as e:
            if self.logger:
                self.logger.warning(f"[builtin_price] 清理PO占位失败: {e}")

        # 清掉本地跳过/硬pass/PO 占位等非柜台行（只保留大 QMT 订单）
        try:
            self._sweep_non_broker_order_rows()
        except Exception as e:
            if self.logger:
                self.logger.warning(f"[builtin_price] 清理非柜台订单行失败: {e}")

        # 执行记录：每笔 passorder 各记一次（与图表回写解耦，避免无图表时下午单丢失）
        for loc in local_orders:
            if not isinstance(loc, dict):
                continue
            msg = str(loc.get("msg") or "").strip()
            status = str(loc.get("status") or "").strip().lower()
            try:
                bst = int(loc.get("broker_status")) if loc.get("broker_status") is not None else -1
            except (TypeError, ValueError):
                bst = -1
            ordered = (
                msg == "passorder_called"
                or status in ("submitted", "filled", "passorder_called", "error", "skipped")
                or bst in (50, 51, 52, 55, 56, 57)
            )
            if not ordered:
                continue
            # 提前挂单不写执行记录；确认/撤销另记
            # 例外：本笔金额低于下限已终结
            if str(loc.get("event_type") or "") == "early_place" and status != "filled":
                if (
                    str(loc.get("cash_block") or "") != "order_below_min"
                    and str(loc.get("order_sysid") or "") != "SKIPPED_MIN_BUY"
                ):
                    continue
            exec_key = "exec|%s" % self._order_identity(loc)
            if exec_key in self._seen_order_keys:
                continue
            self._seen_order_keys.add(exec_key)
            try:
                self._record_builtin_execution(loc)
            except Exception as e:
                if self.logger:
                    self.logger.debug(f"[builtin_price] 执行记录写入失败: {e}")

        # 图表回写：passorder 成功即按 task_id 标已执行（勿等已成），防止规则重装/重载后同规则再打一单
        for loc in local_orders:
            if not isinstance(loc, dict):
                continue
            tid = str(loc.get("task_id") or "").strip()
            code = str(loc.get("stock_code") or "").strip().upper()
            if not tid or not code:
                continue
            msg = str(loc.get("msg") or "").strip()
            status = str(loc.get("status") or "").strip().lower()
            try:
                bst = int(loc.get("broker_status")) if loc.get("broker_status") is not None else -1
            except (TypeError, ValueError):
                bst = -1
            ordered = (
                msg == "passorder_called"
                or status in ("submitted", "filled", "passorder_called", "skipped")
                or bst in (50, 51, 52, 55, 56)
            )
            if not ordered:
                continue
            # 提前挂单不标已执行；确认或提前单成交才回写
            # 例外：本笔金额低于最小买入已终结（SKIPPED_MIN_BUY）
            ev = str(loc.get("event_type") or "")
            if bool(loc.get("early_order")) and ev == "early_place" and status != "filled":
                if (
                    str(loc.get("cash_block") or "") != "order_below_min"
                    and str(loc.get("order_sysid") or "") != "SKIPPED_MIN_BUY"
                ):
                    continue
            if ev == "early_cancel":
                continue
            # 夜市委托 / 定时清仓：须柜台「已报」(50+) 才标已执行；废单继续重试，不回写
            if ev in ("night_buy_hit", "night_sell_hit", "scheduled_clear_hit") or "夜市" in str(
                loc.get("strategy_name") or ""
            ) or "定时清仓" in str(loc.get("strategy_name") or ""):
                if bst == 57:
                    continue
                if bst not in (50, 51, 52, 55, 56) and status != "filled":
                    continue
            # 网格必须按点位去重，否则 g0 回写后会挡住 g1/g2…
            mark_key = "chart|%s" % tid
            if loc.get("grid_index") is not None:
                try:
                    mark_key = "chart|%s|g%d" % (tid, int(loc.get("grid_index")))
                except (TypeError, ValueError):
                    pass
            if mark_key in self._seen_order_keys:
                continue
            self._seen_order_keys.add(mark_key)
            oid = str(loc.get("order_sysid") or self._make_order_id(loc))
            try:
                self._mark_chart_rule_executed(code, tid, loc, oid)
            except Exception as e:
                if self.logger:
                    self.logger.debug(f"[builtin_price] 图表回写失败: {e}")

        self._apply_early_states_snapshot()

    def _apply_early_states_snapshot(self) -> None:
        """把大 QMT early_states 回写到规则（early_order / early_order_price 展示）。"""
        try:
            from utils.ant_rules_io_ext import load_json

            data = load_json(self._results_path) if self._results_path else {}
            states = (data or {}).get("early_states") or {}
        except Exception:
            return
        if not isinstance(states, dict):
            return
        view = self._resolve_charts_view()
        # 先清旧提前态：对所有 armed early keys 缺失的按 inactive 处理太重，
        # 仅对当前 active keys 置为提前；确认/撤单由 order 回写清状态。
        for ekey, st in states.items():
            if not ekey or not isinstance(st, dict) or not bool(st.get("active")):
                continue
            tid = str(st.get("task_id") or ekey.split("@g")[0] or "").strip()
            if not tid:
                continue
            try:
                if view is not None and hasattr(view, "apply_builtin_early_state"):
                    view.apply_builtin_early_state(tid, st)
                else:
                    self._apply_early_state_to_task(tid, st)
            except Exception as e:
                if self.logger:
                    self.logger.debug(f"[builtin_price] early 回写失败 {ekey}: {e}")

    def _apply_early_state_to_task(self, task_id: str, state: dict) -> bool:
        tm = self.task_manager
        if tm is None or not getattr(tm, "tasks", None):
            return False
        tid = str(task_id or "").strip()
        rule_id = tid.split(":")[-1].strip() if tid else ""
        if not rule_id:
            return False
        st = state or {}
        for task in tm.tasks.values():
            if not isinstance(task, dict):
                continue
            params = task.get("params") or {}
            if isinstance(params, str):
                try:
                    import json as _json

                    params = _json.loads(params) if params else {}
                except Exception:
                    params = {}
            rules = params.get("rules") if isinstance(params, dict) else None
            if not isinstance(rules, list):
                continue
            hit = None
            for r in rules:
                if isinstance(r, dict) and str(r.get("id") or "") == rule_id:
                    hit = r
                    break
            if hit is None:
                continue
            if hit.get("executed") or hit.get("scheduled_clear_executed"):
                return True
            uid = str(st.get("user_order_id") or "").strip() or "BUILTIN_EARLY"
            hit["early_order"] = True
            hit["early_order_id"] = uid  # 图表黄色节点依赖此字段
            hit["early_order_price"] = float(st.get("price") or hit.get("price") or 0)
            hit["early_order_submit_volume"] = int(st.get("volume") or hit.get("volume") or 0)
            params["rules"] = rules
            task["params"] = params
            try:
                tm.save_tasks(list(tm.tasks.values()))
            except Exception:
                pass
            return True
        return False

    def _record_builtin_execution(self, loc: dict) -> None:
        from core.execution_record_manager import ExecutionRecordManager

        code = str(loc.get("stock_code") or "").strip().upper()
        stock_name = ""
        rule = None
        tid = str(loc.get("task_id") or "").strip()
        rule_id = tid.split(":")[-1] if tid else ""
        tm = self.task_manager
        if tm is not None and getattr(tm, "tasks", None):
            code6 = code.split(".")[0]
            for task in tm.tasks.values():
                if not isinstance(task, dict):
                    continue
                sc = str(task.get("stock_code") or "").strip().upper()
                if sc.split(".")[0] != code6:
                    continue
                if not stock_name:
                    stock_name = str(task.get("stock_name") or task.get("name") or "")
                params = task.get("params") or {}
                if isinstance(params, str):
                    try:
                        import json as _json

                        params = _json.loads(params) if params else {}
                    except Exception:
                        params = {}
                for r in (params.get("rules") if isinstance(params, dict) else None) or []:
                    if isinstance(r, dict) and rule_id and str(r.get("id") or "") == rule_id:
                        rule = r
                        if not stock_name:
                            stock_name = str(task.get("stock_name") or "")
                        break
                if rule is not None:
                    break
        if not stock_name or stock_name in ("未知名称", "未知"):
            try:
                from utils.stock_info_manager import get_stock_name

                stock_name = get_stock_name(code) or stock_name
            except Exception:
                pass
        oid = str(loc.get("order_sysid") or self._make_order_id(loc))
        mgr = ExecutionRecordManager()
        ok = mgr.record_from_builtin_order(
            loc, order_id=oid, stock_name=stock_name, rule=rule
        )
        if ok and self.logger:
            self.logger.info(
                f"[builtin_price] 执行记录已写入 {code} {loc.get('event_type')} "
                f"{loc.get('volume')}@{loc.get('price')} id={oid}"
            )
    def _format_order_time(self, raw) -> str:
        """兼容 ISO / HH:MM:SS / HHMMSS(5~6位)。"""
        if raw is None:
            return ""
        if isinstance(raw, (int, float)):
            try:
                n = int(raw)
                if n <= 0:
                    return ""
                s = "%06d" % (n % 1000000)
                return "%s:%s:%s" % (s[0:2], s[2:4], s[4:6])
            except (TypeError, ValueError):
                return ""
        at = str(raw).strip()
        if not at or at in ("0", "None", "none"):
            return ""
        if "T" in at:
            return at.split("T", 1)[1][:8]
        if " " in at and ":" in at:
            return at.split(" ")[-1][:8]
        if ":" in at:
            return at[:8] if len(at) >= 8 else at
        digits = "".join(ch for ch in at if ch.isdigit())
        if len(digits) >= 6:
            digits = digits[-6:]
            return "%s:%s:%s" % (digits[0:2], digits[2:4], digits[4:6])
        if len(digits) == 5:
            digits = digits.zfill(6)
            return "%s:%s:%s" % (digits[0:2], digits[2:4], digits[4:6])
        return ""

    def _order_identity(self, rec: dict) -> str:
        # 真实柜台合同号优先；哨兵 SKIPPED_MIN_BUY 等非唯一，必须用 at+task 区分
        from core.execution_record_manager import is_unique_broker_sysid

        sysid = str(rec.get("order_sysid") or "").strip()
        if is_unique_broker_sysid(sysid):
            return "sys:%s" % sysid
        pass_uid = str(rec.get("pass_uid") or "").strip()
        tid = str(rec.get("task_id") or "").strip()
        at = str(rec.get("at") or rec.get("order_time") or "").strip()
        code = str(rec.get("stock_code") or "").strip().upper()
        gi_s = ""
        if rec.get("grid_index") is not None:
            try:
                gi_s = "|g%d" % int(rec.get("grid_index"))
            except (TypeError, ValueError):
                gi_s = ""
        if tid and at:
            return "loc:%s|%s|%s|%s%s" % (tid, at, rec.get("price"), rec.get("volume"), gi_s)
        if tid:
            return "loc:%s|%s|%s|%s|%s%s" % (
                tid,
                at,
                code,
                rec.get("price"),
                rec.get("volume"),
                gi_s,
            )
        if pass_uid and at:
            return "pu:%s|%s|%s%s" % (pass_uid, at, rec.get("volume"), gi_s)
        return "po:%s" % self._make_order_id(rec)

    def _infer_status_from_volume(self, rec: dict) -> str:
        """未知状态码时用成交量兜底，避免界面「买入-未知」。"""
        try:
            tv = int(rec.get("traded_volume") or 0)
            ov = int(rec.get("volume") or 0)
        except (TypeError, ValueError):
            tv, ov = 0, 0
        if ov > 0 and tv >= ov:
            return "已成"
        if tv > 0:
            return "部成"
        return "已报"

    def _resolve_display_status(self, rec: dict) -> tuple:
        """返回 (中文状态, ok_for_trade_vol)。状态必须来自大 QMT，不得臆测已成。"""
        if rec.get("broker_status") is not None and str(rec.get("broker_status")).strip() != "":
            try:
                code = int(rec.get("broker_status"))
            except (TypeError, ValueError):
                code = 255
            text = str(rec.get("broker_status_text") or "").strip()
            if not text or text in ("未知", "None", "none"):
                text = self.ORDER_STATUS_TEXT.get(code, "") or self._infer_status_from_volume(rec)
            return text, code in (55, 56) or text in ("已成", "部成")
        text = str(rec.get("broker_status_text") or "").strip()
        if text and text not in ("未知", "None", "none"):
            return text, text in ("已成", "部成")
        status_raw = str(rec.get("status") or "").strip().lower()
        msg = str(rec.get("msg") or "").strip()
        if status_raw == "error":
            return "废单", False
        if status_raw == "skipped":
            return "已跳过", False
        # passorder 已调用但尚未匹配到柜台委托：不得显示「已成」
        if status_raw in ("submitted", "passorder_called") or msg == "passorder_called":
            return "待查", False
        if status_raw == "filled":
            return "已成", True
        if status_raw == "cancelled":
            return "已撤", False
        if status_raw in ("", "unknown", "none"):
            return self._infer_status_from_volume(rec), False
        return (status_raw or self._infer_status_from_volume(rec)), False

    def _looks_like_order_remark_id(self, text: str) -> bool:
        """柜台 m_strRemark / userOrderId，不应当作说明展示。"""
        s = str(text or "").strip()
        if not s:
            return False
        if s.startswith("蚂蚁-") or "买入" in s or "卖出" in s or "申购" in s or "网格" in s or "单点" in s or "清仓" in s or "提前" in s:
            return False
        if "rule_" in s:
            return True
        # uuid 片段 / 纯十六进制编号
        compact = s.replace("-", "").replace("_", "")
        if len(s) >= 16 and all(ch.isalnum() or ch in "-_" for ch in s):
            if sum(1 for ch in compact if ch.isdigit() or ("a" <= ch.lower() <= "f")) >= max(12, len(compact) * 0.7):
                return True
        return False

    def _resolve_order_reason(self, rec: dict, sysid: str = "") -> str:
        """说明列：策略名，禁止把 userOrderId/remark 当说明。"""
        for key in ("strategy_name", "reason"):
            val = str(rec.get(key) or "").strip()
            if val and not self._looks_like_order_remark_id(val):
                return val
        sid = str(sysid or rec.get("order_sysid") or "").strip()
        if sid:
            for loc in getattr(self, "_last_local_orders", None) or []:
                if not isinstance(loc, dict):
                    continue
                if str(loc.get("order_sysid") or "").strip() != sid:
                    continue
                for key in ("strategy_name", "reason"):
                    val = str(loc.get(key) or "").strip()
                    if val and not self._looks_like_order_remark_id(val):
                        return val
                ev = str(loc.get("event_type") or "").strip()
                if ev == "single_buy_hit":
                    return "蚂蚁-单点买入"
                if ev == "single_sell_hit":
                    return "蚂蚁-单点卖出"
                if ev == "best_sell_hit":
                    return "蚂蚁-弹性卖出"
                if ev == "best_buy_hit":
                    return "蚂蚁-弹性买入"
                if ev == "cage_buy_hit":
                    return "蚂蚁-笼子买入"
                if ev == "cage_sell_hit":
                    return "蚂蚁-笼子卖出"
                if ev == "grid_buy_hit":
                    return "蚂蚁-网格买入"
                if ev == "grid_sell_hit":
                    return "蚂蚁-网格卖出"
                if ev in ("scheduled_clear_hit", "scheduled_clear_skip"):
                    return "蚂蚁-定时清仓"
                if ev == "early_place":
                    side = str(loc.get("side") or "").lower()
                    return "蚂蚁-提前卖出" if side == "sell" else "蚂蚁-提前买入"
                if ev == "early_cancel":
                    return "蚂蚁-提前撤单"
                if ev == "early_confirm":
                    return "蚂蚁-提前确认"
                if ev == "tb_pass":
                    return "蚂蚁-突破买入"
                if ev == "breakthrough_sell_hit":
                    return "蚂蚁-突破卖出"
                if "grid_buy" in ev:
                    return "蚂蚁-网格买入"
                if "grid_sell" in ev:
                    return "蚂蚁-网格卖出"
                if "scheduled_clear" in ev:
                    return "蚂蚁-定时清仓"
        side = str(rec.get("side") or "buy").lower()
        if self._is_subscribe_rec(rec) or side in ("subscribe", "ipo"):
            return "新股申购"
        # 无蚂蚁策略名时：优先柜台 OptName（手机委托等），勿冒充「蚂蚁-单点*」
        opt = str(rec.get("opt_name") or "").strip()
        if opt:
            return opt
        return "卖出" if side == "sell" else "买入"

    def _push_or_refresh_order(self, rec: dict) -> None:
        code = str(rec.get("stock_code") or "").strip().upper()
        if not code:
            return
        identity = self._order_identity(rec)
        order_status, _filled = self._resolve_display_status(rec)
        traded_vol = int(rec.get("traded_volume") or 0)

        sysid = str(rec.get("order_sysid") or "").strip()
        order_time = self._format_order_time(str(rec.get("order_time") or rec.get("at") or "").strip())
        if not order_time and sysid:
            for loc in getattr(self, "_last_local_orders", None) or []:
                if not isinstance(loc, dict):
                    continue
                if str(loc.get("order_sysid") or "").strip() != sysid:
                    continue
                # 优先本地 at（passorder 时刻），避免柜台空时间/错配
                order_time = self._format_order_time(str(loc.get("at") or loc.get("order_time") or ""))
                if order_time:
                    break

        strategy_name = self._resolve_order_reason(rec, sysid)
        state_key = "%s|%s|%s|%s|%s" % (identity, order_status, traded_vol, order_time, strategy_name)
        if self._order_status_seen.get(identity) == state_key:
            return
        self._order_status_seen[identity] = state_key

        # 列表主键与行号：有合同号用合同号；否则用本地唯一指纹，绝不用截断 uuid
        replace_order_id = ""
        if sysid:
            order_id = sysid
            # 本地已匹配到该合同号时，用当初的 PO 指纹把「待查」行就地升级
            for loc in getattr(self, "_last_local_orders", None) or []:
                if not isinstance(loc, dict):
                    continue
                if str(loc.get("order_sysid") or "").strip() != sysid:
                    continue
                replace_order_id = self._make_order_id(loc)
                loc_wo = dict(loc)
                loc_wo["order_sysid"] = ""
                try:
                    self._order_status_seen.pop(self._order_identity(loc_wo), None)
                except Exception:
                    pass
                break
        else:
            order_id = self._make_order_id(rec)

        side = str(rec.get("side") or "buy").lower()
        if self._is_subscribe_rec(rec):
            side = "subscribe"
        trade_type = self._trade_type_from_side(side)
        px = float(rec.get("price") or 0)
        vol = int(rec.get("volume") or 0)
        traded_px = float(rec.get("traded_price") or 0)

        stock_name = str(rec.get("stock_name") or "").strip()
        if not stock_name or stock_name in ("未知名称", "未知"):
            try:
                from utils.stock_info_manager import get_stock_name

                stock_name = get_stock_name(code) or ""
                if stock_name in ("未知名称", "未知"):
                    stock_name = ""
            except Exception:
                stock_name = ""
        # 新股/漏网：至少显示代码，避免「未知名称」
        if not stock_name:
            stock_name = code.split(".")[0] if "." in code else code

        trade_info = {
            "stock_code": code,
            "stock_name": stock_name,
            "order_id": order_id,
            "replace_order_id": replace_order_id,
            "type": trade_type,
            "price": px,
            "volume": vol,
            "order_time": order_time,
            "reason": strategy_name,
            "order_status": order_status,
            "trade_volume": traded_vol,
            "trade_price": traded_px if traded_px > 0 else (px if traded_vol > 0 else 0.0),
            "strategy_name": strategy_name,
            "is_real_order": True,
        }

        ext = self._resolve_ui_ext()
        pushed = False
        if ext is not None and hasattr(ext, "add_trade_record"):
            try:
                ext.add_trade_record(code, trade_info)
                pushed = True
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"[builtin_price] add_trade_record失败: {e}")
        if not pushed and self.task_manager is not None:
            try:
                if hasattr(self.task_manager, "trade_record_updated"):
                    self.task_manager.trade_record_updated.emit(code, trade_info)
                    pushed = True
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"[builtin_price] trade_record_updated失败: {e}")
        # 常规「订单入表」属基础轮询回填，不再打 INFO（失败仍走上面的 warning）

    def _make_order_id(self, rec: dict) -> str:
        import hashlib

        raw = "|".join(
            [
                str(rec.get("at") or ""),
                str(rec.get("task_id") or ""),
                str(rec.get("price") or ""),
                str(rec.get("volume") or ""),
                str(rec.get("side") or ""),
            ]
        )
        digest = hashlib.md5(raw.encode("utf-8", errors="ignore")).hexdigest()[:10]
        at = str(rec.get("at") or "").replace(":", "").replace("T", "")[-6:]
        return f"PO{at}{digest}"

    def _sweep_non_broker_order_rows(self) -> None:
        """订单表只保留柜台合同号行：删掉跳过哨兵、已跳过、PO 占位。"""
        from core.execution_record_manager import PSEUDO_ORDER_SYSIDS, is_unique_broker_sysid

        ext = self._resolve_ui_ext()
        table = getattr(ext, "tableWidget_3", None) if ext is not None else None
        if table is None or table.rowCount() <= 0:
            return
        drop_rows = []
        for row in range(table.rowCount()):
            oid = ""
            status = ""
            try:
                oid = table.item(row, 0).text().strip() if table.item(row, 0) else ""
            except Exception:
                oid = ""
            try:
                # 状态列：与 add_trade_record 一致，常见为第 6 列
                status = table.item(row, 6).text().strip() if table.item(row, 6) else ""
            except Exception:
                status = ""
            if not oid and not status:
                continue
            if (
                oid in PSEUDO_ORDER_SYSIDS
                or "已跳过" in status
                or "硬pass" in status
                or "价格带放弃" in status
            ):
                drop_rows.append(row)
                continue
            if oid.startswith("PO") or not is_unique_broker_sysid(oid):
                # 无真实柜台合同号的本地行不进订单列表
                drop_rows.append(row)
        for row in reversed(drop_rows):
            try:
                table.removeRow(row)
            except Exception:
                pass

    def _sweep_orphan_po_rows(self, display_recs=None) -> None:
        """若柜台合同号行已在表中，删除对应 PO 占位行（历史成对残留）。"""
        ext = self._resolve_ui_ext()
        table = getattr(ext, "tableWidget_3", None) if ext is not None else None
        if table is None or table.rowCount() <= 0:
            return

        from core.execution_record_manager import is_unique_broker_sysid

        # 本轮展示里已有的真实合同号（哨兵不算）
        live_sysids = set()
        for rec in display_recs or []:
            if not isinstance(rec, dict):
                continue
            sid = str(rec.get("order_sysid") or "").strip()
            if is_unique_broker_sysid(sid):
                live_sysids.add(sid)

        # 本地已匹配合同号 → 当初 PO 指纹
        po_to_drop = set()
        for loc in getattr(self, "_last_local_orders", None) or []:
            if not isinstance(loc, dict):
                continue
            sid = str(loc.get("order_sysid") or "").strip()
            if not is_unique_broker_sysid(sid):
                continue
            if live_sysids and sid not in live_sysids:
                # 合同号未必都在 display（过滤后）；仍按「表内已有该合同号」决定
                pass
            po_to_drop.add(self._make_order_id(loc))
            loc_wo = dict(loc)
            loc_wo["order_sysid"] = ""
            try:
                self._order_status_seen.pop(self._order_identity(loc_wo), None)
            except Exception:
                pass

        # 表内已有哪些真实合同号 / PO
        sysid_rows = {}
        po_rows = []
        for row in range(table.rowCount()):
            oid = table.item(row, 0).text().strip() if table.item(row, 0) else ""
            code = table.item(row, 1).text().strip().upper() if table.item(row, 1) else ""
            status = table.item(row, 6).text() if table.item(row, 6) else ""
            if not oid:
                continue
            if oid.startswith("PO"):
                po_rows.append((row, oid, code, status))
            else:
                sysid_rows[oid] = (row, code, status)

        drop_rows = set()
        for row, oid, code, status in po_rows:
            if oid in po_to_drop:
                # 仅当对应合同号已在表内时删；否则保留待查（柜台尚未回报）
                matched = False
                for loc in getattr(self, "_last_local_orders", None) or []:
                    if not isinstance(loc, dict):
                        continue
                    if self._make_order_id(loc) != oid:
                        continue
                    sid = str(loc.get("order_sysid") or "").strip()
                    if sid and sid in sysid_rows:
                        matched = True
                        break
                if matched:
                    drop_rows.add(row)
                    continue
            # 兜底：同代码、同方向数量价格、PO 仍待查，且已有已成/已报真实行
            if "待查" not in (status or ""):
                continue
            try:
                qty_text = table.item(row, 5).text() if table.item(row, 5) else ""
                ordered_vol = int(str(qty_text).split("/")[0].strip())
                px_text = table.item(row, 4).text() if table.item(row, 4) else ""
                ordered_px = float(str(px_text).split("/")[0].strip())
            except (TypeError, ValueError):
                continue
            side = "卖出" if "卖出" in status else ("买入" if "买入" in status else "")
            for sid, (srow, scode, sstatus) in sysid_rows.items():
                if scode != code:
                    continue
                if side and side not in (sstatus or ""):
                    continue
                try:
                    sq = table.item(srow, 5).text() if table.item(srow, 5) else ""
                    svol = int(str(sq).split("/")[0].strip())
                    sp = table.item(srow, 4).text() if table.item(srow, 4) else ""
                    spx = float(str(sp).split("/")[0].strip())
                except (TypeError, ValueError):
                    continue
                if svol != ordered_vol:
                    continue
                if abs(spx - ordered_px) > 0.011:
                    continue
                drop_rows.add(row)
                break

        for row in sorted(drop_rows, reverse=True):
            try:
                table.removeRow(row)
            except Exception:
                pass

    def _apply_elastic_states_snapshot(self) -> None:
        """把大 QMT elastic_states 回写到图表规则（triggered→节点变红，极值→动态回落/反弹线）。"""
        try:
            states = load_elastic_states_snapshot(self._results_path)
        except Exception:
            return
        if not isinstance(states, dict) or not states:
            return
        view = self._resolve_charts_view()
        for tid, st in states.items():
            if not tid or not isinstance(st, dict):
                continue
            if not bool(st.get("triggered")):
                continue
            try:
                if view is not None and hasattr(view, "apply_builtin_elastic_state"):
                    view.apply_builtin_elastic_state(str(tid), st)
                else:
                    self._apply_elastic_state_to_task(str(tid), st)
            except Exception as e:
                if self.logger:
                    self.logger.debug(f"[builtin_price] elastic 回写失败 {tid}: {e}")

    def _apply_elastic_state_to_task(self, task_id: str, state: dict) -> bool:
        tm = self.task_manager
        if tm is None or not getattr(tm, "tasks", None):
            return False
        tid = str(task_id or "").strip()
        rule_id = tid.split(":")[-1].strip() if tid else ""
        parent_id = tid.split(":", 1)[0].strip() if ":" in tid else ""
        if not rule_id:
            return False
        st = state or {}
        kind = str(st.get("kind") or "").strip()
        for task in tm.tasks.values():
            if not isinstance(task, dict):
                continue
            if parent_id and str(task.get("task_id") or "") != parent_id:
                continue
            params = task.get("params") or {}
            if isinstance(params, str):
                try:
                    import json as _json

                    params = _json.loads(params) if params else {}
                except Exception:
                    params = {}
            rules = params.get("rules") if isinstance(params, dict) else None
            if not isinstance(rules, list):
                continue
            for r in rules:
                if not isinstance(r, dict) or str(r.get("id") or "") != rule_id:
                    continue
                if r.get("executed"):
                    return False
                changed = self._merge_elastic_into_rule(r, st, kind)
                if changed:
                    params["rules"] = rules
                    task["params"] = params
                    try:
                        tm.save_tasks(list(tm.tasks.values()))
                    except Exception:
                        pass
                return changed
        return False

    @staticmethod
    def _merge_elastic_into_rule(rule: dict, st: dict, kind: str = "") -> bool:
        """把 QMT 弹性状态合并到规则；返回是否有可视化相关变更。"""
        if not isinstance(rule, dict) or not isinstance(st, dict):
            return False
        rtype = str(rule.get("type") or "").strip()
        if not kind:
            if st.get("lowest_price") is not None and st.get("highest_price") is None:
                kind = "best_buy"
            elif rtype == "best_buy":
                kind = "best_buy"
            else:
                kind = "best_sell"
        changed = False
        new_trig = bool(st.get("triggered"))
        if bool(rule.get("triggered")) != new_trig:
            rule["triggered"] = new_trig
            changed = True
        if kind == "best_buy":
            for key in ("lowest_price", "lowest_tick_idx", "tick_idx", "rebound_hit_count"):
                if key not in st:
                    continue
                val = st.get(key)
                old = rule.get(key)
                if key.endswith("price"):
                    try:
                        if abs(float(old or 0) - float(val or 0)) > 1e-9 or (
                            old is None and val is not None
                        ):
                            rule[key] = float(val) if val is not None else None
                            changed = True
                    except (TypeError, ValueError):
                        pass
                else:
                    try:
                        if int(old or 0) != int(val or 0):
                            rule[key] = int(val or 0)
                            changed = True
                    except (TypeError, ValueError):
                        pass
        else:
            for key in ("highest_price", "highest_tick_idx", "tick_idx", "pullback_hit_count"):
                if key not in st:
                    continue
                val = st.get(key)
                old = rule.get(key)
                if key.endswith("price"):
                    try:
                        if abs(float(old or 0) - float(val or 0)) > 1e-9 or (
                            old is None and val is not None
                        ):
                            rule[key] = float(val) if val is not None else None
                            changed = True
                    except (TypeError, ValueError):
                        pass
                else:
                    try:
                        if int(old or 0) != int(val or 0):
                            rule[key] = int(val or 0)
                            changed = True
                    except (TypeError, ValueError):
                        pass
        return changed

    def _mark_chart_rule_executed(
        self,
        stock_code: str,
        task_id: str,
        rec: Dict[str, Any],
        order_id: str,
    ) -> None:
        ok = False
        view = self._resolve_charts_view()
        if view is not None and hasattr(view, "apply_builtin_order_feedback"):
            try:
                ok = bool(view.apply_builtin_order_feedback(stock_code, task_id, rec, order_id))
                if self.logger:
                    self.logger.info(
                        f"[builtin_price] 图表回写 {'ok' if ok else 'miss'} "
                        f"{stock_code} task={task_id}"
                    )
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"[builtin_price] 图表回写失败: {e}")
        # 图表尚未创建时，直接改 TaskManager 规则并重写 rules_armed，避免同规则连环下单
        if not ok:
            try:
                from ui.tasks_charts_view import TasksChartsView

                helper = TasksChartsView.__new__(TasksChartsView)
                helper.task_manager = self.task_manager
                helper.logger = self.logger
                ok = bool(
                    helper._apply_builtin_order_feedback_to_task(
                        stock_code, task_id, rec, order_id
                    )
                )
                if ok and self.logger:
                    self.logger.info(
                        f"[builtin_price] 任务回写 ok {stock_code} task={task_id}"
                    )
            except Exception as e:
                if self.logger:
                    self.logger.warning(f"[builtin_price] 任务回写失败: {e}")
        if ok:
            try:
                from utils.rules_armed_sync import sync_rules_armed

                sync_rules_armed(self.task_manager, getattr(self, "qmt_adapter", None))
            except Exception as e:
                if self.logger:
                    self.logger.debug(f"[builtin_price] sync_rules_armed: {e}")

    @staticmethod
    def _build_tick_data(
        stock_code: str,
        price: float,
        tick_time: str,
        *,
        intraday: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        iday = intraday or {}
        dt: Optional[datetime] = None
        if tick_time:
            try:
                parts = tick_time.split(":")
                if len(parts) >= 2:
                    hour = int(parts[0])
                    minute = int(parts[1])
                    second = int(parts[2]) if len(parts) > 2 else 0
                    beijing = timezone(timedelta(hours=8))
                    today = datetime.now(beijing).date()
                    dt = datetime(
                        today.year,
                        today.month,
                        today.day,
                        hour,
                        minute,
                        second,
                        tzinfo=beijing,
                    )
            except (TypeError, ValueError):
                dt = None
        if dt is None:
            dt = datetime.now(timezone(timedelta(hours=8)))
        return {
            "stock_code": stock_code,
            "lastPrice": float(price),
            "lastClose": 0,
            "open": float(iday.get("open") or 0),
            "high": float(iday.get("high") or 0),
            "low": float(iday.get("low") or 0),
            "askPrice": [],
            "bidPrice": [],
            "askVol": [],
            "bidVol": [],
            "time": dt,
        }
