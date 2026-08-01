# -*- coding: utf-8 -*-
"""
券商网关薄封装：mini 模式透传 QMTManager，builtin 模式集中管理现价轮询。

调用方继续用 gateway.trade / tick_data_signal 等，无需关心底层是 xtdata 还是 results.json。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Set

from brokers.qmt_adapter import QMTManager
from utils.qmt_execution_config import get_qmt_mode, use_builtin_price_feed


class BrokerGateway:
    """QMT 网关门面；mini 模式几乎零开销透传。"""

    def __init__(self, impl: QMTManager):
        self._impl = impl
        self._builtin_price_poller = None

    @property
    def impl(self) -> QMTManager:
        return self._impl

    def __getattr__(self, name: str) -> Any:
        return getattr(self._impl, name)

    # —— 行情订阅状态（替代直接访问 subscribe_thread 内部字段）——

    def get_subscribed_codes(self) -> Set[str]:
        sub = getattr(self._impl, "subscribe_thread", None)
        raw = getattr(sub, "_stock_codes", None) if sub is not None else None
        if not raw:
            return set()
        return set(raw)

    def is_subscribed(self, stock_code: str) -> bool:
        code = str(stock_code or "").strip().upper()
        if not code:
            return False
        return code in self.get_subscribed_codes()

    def get_latest_tick_time_str(self) -> str:
        sub = getattr(self._impl, "subscribe_thread", None)
        if sub is None:
            return ""
        return str(getattr(sub, "latest_tick_time_str", "") or "")

    def is_ready(self) -> bool:
        return bool(getattr(self._impl, "_is_initialized", False))

    def is_quote_feed_alive(self) -> bool:
        sub = getattr(self._impl, "subscribe_thread", None)
        return bool(sub is not None and sub.is_alive())

    def add_subscribe_codes(
        self,
        stock_codes: Iterable[str],
        *,
        force_update: bool = True,
    ) -> bool:
        """将股票并入订阅列表；有变化时返回 True。"""
        to_add = {
            str(code or "").strip().upper()
            for code in (stock_codes or [])
            if str(code or "").strip()
        }
        if not to_add:
            return False
        current = self.get_subscribed_codes()
        merged = current | to_add
        if merged == current:
            return False
        self.update_subscribe_stocks(list(merged), force_update=force_update)
        return True

    def ensure_subscribed(self, stock_code: str, *, force_update: bool = True) -> bool:
        return self.add_subscribe_codes([stock_code], force_update=force_update)

    def remove_subscribe_codes(
        self,
        stock_codes: Iterable[str],
        *,
        force_update: bool = True,
    ) -> bool:
        """从订阅列表移除股票；有变化时返回 True。"""
        to_remove = {
            str(code or "").strip().upper()
            for code in (stock_codes or [])
            if str(code or "").strip()
        }
        if not to_remove:
            return False
        current = self.get_subscribed_codes()
        merged = current - to_remove
        if merged == current:
            return False
        self.update_subscribe_stocks(list(merged), force_update=force_update)
        return True

    def stop_quote_feed(self) -> None:
        sub = getattr(self._impl, "subscribe_thread", None)
        if sub is not None and hasattr(sub, "stop"):
            sub.stop()

    def get_latest_price(self, stock_code: str) -> float:
        code = str(stock_code or "").strip().upper()
        if not code:
            return 0.0
        tm = getattr(self._impl, "task_manager", None)
        if tm is not None:
            prices = getattr(tm, "latest_prices", None) or {}
            if code in prices:
                try:
                    val = float(prices[code] or 0)
                    if val > 0:
                        return val
                except (TypeError, ValueError):
                    pass
        snap = self.get_tick_data(code)
        if snap:
            try:
                return float(snap.get("lastPrice") or snap.get("last_price") or 0)
            except (TypeError, ValueError):
                return 0.0
        return 0.0

    def get_tick_data(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """当前价快照；优先 task_manager 缓存，否则 xtdata.get_full_tick。"""
        code = str(stock_code or "").strip().upper()
        if not code:
            return None
        tm = getattr(self._impl, "task_manager", None)
        if tm is not None:
            prices = getattr(tm, "latest_prices", None) or {}
            if code in prices:
                try:
                    val = float(prices[code] or 0)
                    if val > 0:
                        return {"stock_code": code, "lastPrice": val, "last_price": val}
                except (TypeError, ValueError):
                    pass
        try:
            from xtquant import xtdata

            tick = xtdata.get_full_tick([code])
            if tick and code in tick:
                row = tick[code]
                raw = row.get("lastPrice") if row.get("lastPrice") is not None else row.get("last_price")
                if raw is not None:
                    val = float(raw)
                    if val > 0:
                        return {"stock_code": code, "lastPrice": val, "last_price": val}
        except Exception:
            pass
        return None

    def get_execution_mode(self) -> str:
        return get_qmt_mode()

    # —— builtin 现价心跳（results.json → tick_data_signal）——

    def start_builtin_price_feed(self, task_manager, main_window=None, parent=None) -> None:
        if not use_builtin_price_feed():
            return
        if self._builtin_price_poller is not None:
            return
        from brokers.builtin_price_feed import BuiltinPricePoller

        self._builtin_price_poller = BuiltinPricePoller(
            task_manager,
            self,
            main_window,
            parent=parent,
        )
        self._builtin_price_poller.start()

    def stop_builtin_price_feed(self) -> None:
        poller = self._builtin_price_poller
        if poller is None:
            return
        try:
            poller.stop()
        except Exception:
            pass
        self._builtin_price_poller = None

    def stop(self) -> None:
        self.stop_builtin_price_feed()
        self._impl.stop()


def create_broker_gateway(
    path: str,
    account_id: str,
    *,
    mode: str = "live",
) -> BrokerGateway:
    """工厂：创建网关；默认 mini 透传 QMTManager。"""
    impl = QMTManager(path, account_id, mode=mode)
    return BrokerGateway(impl)
