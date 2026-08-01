"""BrokerGateway 结构冒烟测试（不启动 QThread，不连 QMT）。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_delegation() -> bool:
    from brokers.broker_gateway import BrokerGateway
    from brokers.qmt_adapter import QMTManager

    impl = MagicMock(spec=QMTManager)
    impl.trade.return_value = "order-1"
    impl._is_initialized = True
    impl.subscribe_thread = MagicMock()
    impl.subscribe_thread._stock_codes = {"600519.SH", "000001.SZ"}
    impl.subscribe_thread.latest_tick_time_str = "10:15:30"

    gw = BrokerGateway(impl)
    assert gw.trade("600519.SH", 23, 100.0, 100, "test") == "order-1"
    assert gw.is_ready() is True
    assert gw.get_subscribed_codes() == {"600519.SH", "000001.SZ"}
    assert gw.is_subscribed("600519.SH") is True
    assert gw.get_latest_tick_time_str() == "10:15:30"
    assert gw.is_quote_feed_alive() is True
    impl.update_subscribe_stocks = MagicMock()
    assert gw.ensure_subscribed("000001.SZ") is False  # already in set
    assert gw.add_subscribe_codes(["601318.SH"]) is True
    impl.update_subscribe_stocks.assert_called_once()
    assert gw.get_execution_mode() in ("mini", "builtin", "standalone")
    print("  OK  委托 / 订阅状态门面")
    return True


def test_factory_import() -> bool:
    from brokers.broker_gateway import create_broker_gateway

    assert callable(create_broker_gateway)
    print("  OK  create_broker_gateway 可导入")
    return True


def main() -> None:
    print("BrokerGateway 冒烟测试")
    ok = test_factory_import() and test_delegation()
    print("\n=== 汇总 ===")
    print(f"  结构测试: {'通过' if ok else '失败'}")
    if ok:
        print("  mini 实盘请照常启动主程序验证；QMT 连通性仍用 tools/test_qmt_connection.py")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
