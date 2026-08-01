"""BrokerGateway + 真实 MiniQMT 联调（无 GUI，约 20 秒）。"""
from __future__ import annotations

import configparser
import sys
import time
from pathlib import Path

from PyQt5.QtCore import QCoreApplication, QTimer
from PyQt5.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load_config() -> tuple[str, str]:
    cfg = configparser.ConfigParser()
    cfg.read(ROOT / "data" / "config.ini", encoding="utf-8")
    path = cfg.get("Account", "path_qmt", fallback="").strip()
    account = cfg.get("Account", "account_id", fallback="").strip()
    mode = cfg.get("Account", "qmt_mode", fallback="mini").strip()
    return path, account, mode


def main() -> int:
    print("BrokerGateway 实盘联调（MiniQMT）")
    path, account, qmt_mode = _load_config()
    print(f"  path_qmt   = {path}")
    print(f"  account_id = {account}")
    print(f"  qmt_mode   = {qmt_mode}")

    if not path or not account:
        print("FAIL config.ini 缺少 path_qmt / account_id")
        return 1

    app = QApplication.instance() or QApplication(sys.argv)

    from brokers.broker_gateway import create_broker_gateway
    from utils.qmt_execution_config import use_builtin_price_feed

    gw = create_broker_gateway(path, account)
    results: dict[str, object] = {"ok": False}

    def _finish(code: int, msg: str) -> None:
        results["code"] = code
        results["msg"] = msg
        app.quit()

    def _step_check() -> None:
        try:
            print("\n=== 1. 网关门面 ===")
            print(f"  is_ready        = {gw.is_ready()}")
            print(f"  execution_mode  = {gw.get_execution_mode()}")
            print(f"  builtin_feed    = {use_builtin_price_feed()}")

            print("\n=== 2. 资产查询（经网关） ===")
            asset = gw.get_asset()
            if asset:
                print(
                    f"  OK  总资产={asset.get('total_asset')}  "
                    f"可用={asset.get('cash')}"
                )
            else:
                print("  WARN get_asset 返回空，等待连接…")
                asset2, pos = gw.get_asset_positions()
                if asset2:
                    print(
                        f"  OK  总资产={asset2.get('total_asset')}  "
                        f"持仓={len(pos or {})}只"
                    )
                else:
                    print("  FAIL 无法获取资产")
                    _finish(1, "asset")
                    return

            print("\n=== 3. 行情订阅（经网关） ===")
            test_code = "600519.SH"
            gw.update_subscribe_stocks([test_code], force_update=True)
            time.sleep(3)
            subscribed = gw.get_subscribed_codes()
            print(f"  subscribed = {subscribed}")
            if test_code not in subscribed:
                print(f"  WARN {test_code} 未在订阅列表（builtin 模式会跳过 xtdata）")
            else:
                print(f"  OK  已订阅 {test_code}")

            tick_time = gw.get_latest_tick_time_str()
            latest = getattr(gw.task_manager, "latest_prices", {}) if hasattr(gw, "task_manager") else {}
            # task_manager 未挂接时从 impl 侧无 latest_prices；看 subscribe_thread
            print(f"  latest_tick_time_str = {tick_time or '(尚无)'}")

            print("\n=== 4. 透传验证 ===")
            print(f"  gateway.impl type = {type(gw.impl).__name__}")
            print(f"  trade callable    = {callable(gw.trade)}")

            print("\n=== 汇总 ===")
            print("  通过：网关透传 + MiniQMT 连接正常")
            _finish(0, "ok")
        except Exception as e:
            print(f"  FAIL 异常: {e}")
            import traceback
            traceback.print_exc()
            _finish(1, str(e))
        finally:
            try:
                gw.stop()
            except Exception:
                pass

    gw.start()
    QTimer.singleShot(8000, _step_check)

    code = app.exec_()
    exit_code = int(results.get("code", code if code else 1))
    if exit_code == 0:
        print(results.get("msg", ""))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
