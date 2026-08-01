"""简单测试大 QMT / xtquant 连通性（xtdata + xttrader）。"""
from __future__ import annotations

import configparser
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

QMT_INSTALLS = [
    ("大QMT", Path(r"D:/国金证券QMT交易端")),
    ("副本(原mini)", Path(r"D:/国金证券QMT交易端 - 副本")),
]


def _load_account() -> tuple[str, str]:
    cfg = configparser.ConfigParser()
    cfg.read(ROOT / "data" / "config.ini", encoding="utf-8")
    acc = cfg.get("Account", "account_id", fallback="").strip()
    path = cfg.get("Account", "path_qmt", fallback="").strip()
    return acc, path


def test_xtdata() -> bool:
    print("\n=== 1. xtdata 行情 ===")
    try:
        from xtquant import xtdata

        xtdata.enable_hello = False
    except ImportError as e:
        print(f"  FAIL 无法 import xtdata: {e}")
        return False

    code = "600519.SH"
    try:
        tick = xtdata.get_full_tick([code])
        if not tick or code not in tick:
            print(f"  FAIL get_full_tick 无数据（客户端是否已登录？）")
            return False
        row = tick[code]
        lp = row.get("lastPrice") or row.get("last_price")
        print(f"  OK  {code} lastPrice={lp}")
    except Exception as e:
        print(f"  FAIL get_full_tick: {e}")
        return False

    try:
        dates = xtdata.get_trading_dates("SH", "20260101", "20261231")
        print(f"  OK  交易日历 {len(dates)} 天")
    except Exception as e:
        print(f"  WARN get_trading_dates: {e}")

    return True


def _running_qmt_processes() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    try:
        import psutil

        for p in psutil.process_iter(["name", "exe"]):
            name = (p.info.get("name") or "").lower()
            if name in ("xtitclient.exe", "xtminiqmt.exe"):
                out.append((p.info.get("name") or name, p.info.get("exe") or ""))
    except Exception:
        pass
    return out


def test_xttrader(label: str, userdata_path: Path, account_id: str) -> bool:
    print(f"\n=== 2. xttrader [{label}] ===")
    print(f"  path: {userdata_path}")
    if not userdata_path.is_dir():
        print("  FAIL 目录不存在")
        return False

    try:
        from xtquant.xttrader import XtQuantTrader
        from xtquant.xttype import StockAccount
    except ImportError as e:
        print(f"  FAIL import: {e}")
        return False

    session = int(time.time())
    trader = XtQuantTrader(str(userdata_path), session)
    trader.start()
    rc = trader.connect()
    print(f"  connect() = {rc}  (0=成功)")

    ok = rc == 0
    if not ok:
        trader.stop()
        return False

    acc = StockAccount(account_id, "STOCK")
    sub = trader.subscribe(acc)
    print(f"  subscribe() = {sub}  (0=成功)")

    asset = trader.query_stock_asset(acc)
    if asset:
        print(f"  OK  总资产={getattr(asset, 'total_asset', '?')}  可用={getattr(asset, 'cash', '?')}")
    else:
        print("  WARN query_stock_asset 返回空（可能未登录或账号不对）")

    pos = trader.query_stock_positions(acc) or []
    print(f"  OK  持仓 {len(pos)} 只")

    trader.stop()
    return rc == 0 and sub == 0


def main() -> None:
    print("QMT 连通性测试")
    procs = _running_qmt_processes()
    if procs:
        print("当前进程:")
        for name, exe in procs:
            print(f"  {name} -> {exe}")
    else:
        print("当前未发现 XtItClient / XtMiniQmt 进程")

    account_id, cfg_path = _load_account()
    print(f"config account_id = {account_id}")
    print(f"config path_qmt   = {cfg_path}")

    d_ok = test_xtdata()

    results: dict[str, bool] = {}
    for install_name, base in QMT_INSTALLS:
        if not base.is_dir():
            print(f"\n跳过 {install_name}（目录不存在）: {base}")
            continue
        for sub in ("userdata_mini", "userdata"):
            p = base / sub
            if not p.is_dir():
                continue
            key = f"{install_name}/{sub}"
            results[key] = test_xttrader(key, p, account_id)

    print("\n=== 汇总 ===")
    print(f"  xtdata: {'通过' if d_ok else '失败'}")
    for label, ok in results.items():
        print(f"  xttrader {label}: {'通过' if ok else '失败'}")

    if d_ok and any(results.values()):
        winner = next(k for k, v in results.items() if v)
        print(f"\n建议: 将 config.ini path_qmt 改为对应目录（当前可用: {winner}）")
    elif d_ok:
        print("\n行情可用但交易未连上：请确认大 QMT 已登录、程序化权限已开。")
    else:
        print("\n请先确认大 QMT 客户端已启动并完成登录。")


if __name__ == "__main__":
    main()
