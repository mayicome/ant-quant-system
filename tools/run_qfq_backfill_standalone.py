# -*- coding: utf-8 -*-
"""后台跑前复权日线一次性补齐（无需在 QMT 里挂策略）。

依赖：大 QMT 或 miniQMT 已启动且 xtdata 可连（与日常 mini 回退相同）。
逻辑复用 ant_daily_qfq_backfill_once.py，用 xtdata 模拟 ContextInfo。

用法：
  python tools/run_qfq_backfill_standalone.py
  python tools/run_qfq_backfill_standalone.py --wait-qmt 600
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "qmt_builtin" / "src"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

QMT_BIN = Path(r"D:\国金证券QMT交易端\bin.x64")
QMT_CANDIDATES = [
    QMT_BIN / "XtItClient.exe",
    QMT_BIN / "XtMiniQmt.exe",
]


class _XtDataContext:
    """把 xtdata.get_market_data_ex 包装成 ContextInfo 接口。"""

    def __init__(self, xtdata):
        self._xt = xtdata

    def set_universe(self, codes):
        del codes

    def get_market_data_ex(self, *args, **kwargs):
        xt = self._xt
        if args and not kwargs:
            # positional: fields, codes, period, start, end, count, div, fill, subscribe
            if len(args) >= 7:
                fields, codes, period, start_time, end_time, count, div = args[:7]
                kwargs = {
                    "field_list": fields or [],
                    "stock_list": list(codes),
                    "period": period,
                    "start_time": start_time,
                    "end_time": end_time,
                    "count": count,
                    "dividend_type": div,
                }
        field_list = kwargs.pop("field_list", kwargs.pop("fields", []))
        stock_list = kwargs.pop("stock_list", kwargs.pop("stock_code", []))
        period = kwargs.get("period", "1d")
        start_time = kwargs.get("start_time", "")
        end_time = kwargs.get("end_time", "")
        div = kwargs.get("dividend_type", "none")
        codes = [str(c).strip().upper() for c in stock_list if str(c or "").strip()]
        if not codes:
            return {}
        s = str(start_time or "").strip()
        e = str(end_time or "").strip()
        if s and e:
            for code in codes:
                try:
                    xt.download_history_data(code, period, s, e)
                except Exception:
                    pass
        attempts = (
            {"field_list": field_list, "stock_list": codes, **kwargs},
            {"field_list": [], "stock_list": codes, **kwargs},
        )
        last_err = None
        for kw in attempts:
            try:
                return xt.get_market_data_ex([], codes, period, s, e, kwargs.get("count", -1), div)
            except TypeError:
                try:
                    return xt.get_market_data_ex(
                        field_list or [],
                        codes,
                        period,
                        s,
                        e,
                        kwargs.get("count", -1),
                        dividend_type=div,
                    )
                except Exception as ex:
                    last_err = ex
            except Exception as ex:
                last_err = ex
        if last_err:
            raise last_err
        return {}


def _qmt_running() -> bool:
    try:
        import psutil

        for p in psutil.process_iter(["name"]):
            n = (p.info.get("name") or "").lower()
            if n in ("xtitclient.exe", "xtminiqmt.exe"):
                return True
    except Exception:
        pass
    return False


def _try_launch_qmt() -> None:
    if _qmt_running():
        print("[qfq补齐] QMT 进程已在运行")
        return
    for exe in QMT_CANDIDATES:
        if not exe.is_file():
            continue
        try:
            os.spawnl(os.P_NOWAIT, str(exe), str(exe))
            print("[qfq补齐] 已启动 %s，等待登录…" % exe.name)
            return
        except Exception as e:
            print("[qfq补齐] 启动失败 %s: %s" % (exe, e))
    print("[qfq补齐] 未找到 QMT 可执行文件，请手动启动客户端")


def _wait_xtdata(timeout_sec: int) -> object:
    deadline = time.time() + max(1, timeout_sec)
    last_err = ""
    while time.time() < deadline:
        try:
            from xtquant import xtdata

            xtdata.enable_hello = False
            tick = xtdata.get_full_tick(["000001.SZ"])
            if tick and "000001.SZ" in tick:
                print("[qfq补齐] xtdata 已连通")
                return xtdata
        except Exception as e:
            last_err = str(e)
        time.sleep(5.0)
    raise RuntimeError("xtdata 连接超时: %s" % (last_err or "unknown"))


def main() -> int:
    parser = argparse.ArgumentParser(description="后台前复权日线一次性补齐")
    parser.add_argument(
        "--wait-qmt",
        type=int,
        default=300,
        help="等待 QMT/xtdata 连通秒数（默认 300）",
    )
    parser.add_argument(
        "--no-launch",
        action="store_true",
        help="不自动启动 QMT 进程",
    )
    args = parser.parse_args()

    import ant_daily_qfq_backfill_once as bf

    if os.path.isfile(bf._done_path()):
        print("[qfq补齐] 已完成:", bf._done_path())
        return 0

    if not args.no_launch:
        _try_launch_qmt()

    xtdata = _wait_xtdata(args.wait_qmt)
    ctx = _XtDataContext(xtdata)

    bf.on_init(ctx)
    tick_n = 0
    while not os.path.isfile(bf._done_path()):
        bf.on_tick(ctx)
        tick_n += 1
        if tick_n % 30 == 0:
            prog = bf._load_progress()
            print(
                "[qfq补齐] tick=%d phase=%s idx=%s/%s ok_cache=%s ok_full=%s"
                % (
                    tick_n,
                    prog.get("phase"),
                    prog.get("idx"),
                    len(prog.get("codes") or []),
                    prog.get("ok_cache"),
                    prog.get("ok_full"),
                )
            )
        time.sleep(0.05)

    print("[qfq补齐] 全部完成 →", bf._done_path())
    print("可执行: python tools/remove_qfq_backfill_from_qmt.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
