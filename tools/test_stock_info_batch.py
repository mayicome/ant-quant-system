# -*- coding: utf-8 -*-
"""测试 stock_info_manager 批量 get_instrument_detail_list 是否可用。"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _load_xtdata():
    try:
        import xtquant.xtdata as xtdata
        try:
            xtdata.enable_hello = False
        except Exception:
            pass
        return xtdata
    except ImportError as e:
        print(f"FAIL 无法 import xtdata: {e}")
        return None


def test_batch_api(sample_size: int = 20) -> bool:
    xtdata = _load_xtdata()
    if xtdata is None:
        return False

    has_list_api = callable(getattr(xtdata, "get_instrument_detail_list", None))
    print(f"get_instrument_detail_list 存在: {has_list_api}")
    if not has_list_api:
        print("WARN 当前 xtquant 无批量接口，将走分块逐只回退")

    try:
        raw = xtdata.get_stock_list_in_sector("沪深A股") or []
    except Exception as e:
        print(f"FAIL get_stock_list_in_sector: {e}")
        print("提示: 需开启 MiniQMT 极简模式或投研版，仅大 QMT 主界面不够")
        return False

    if not raw:
        print("FAIL 沪深A股列表为空")
        return False

    sample = list(raw[:sample_size])
    print(f"样本数量: {len(sample)}  示例: {sample[:3]}")

    if has_list_api:
        t0 = time.time()
        try:
            details = xtdata.get_instrument_detail_list(sample) or {}
        except Exception as e:
            print(f"FAIL get_instrument_detail_list 调用异常: {e}")
            return False
        elapsed = time.time() - t0
        named = sum(
            1
            for d in details.values()
            if isinstance(d, dict) and (d.get("InstrumentName") or d.get("instrumentName"))
        )
        print(f"批量接口: 返回 {len(details)} 条, 有名称 {named} 条, 耗时 {elapsed:.2f}s")
        if named <= 0:
            print("FAIL 批量接口未返回有效名称")
            return False
    else:
        print("跳过批量直调测试（接口不存在）")

    from utils.stock_info_manager import _fetch_instrument_details_batched

    t0 = time.time()
    batched = _fetch_instrument_details_batched(xtdata, sample, chunk_size=max(5, sample_size))
    elapsed = time.time() - t0
    named2 = sum(
        1
        for d in batched.values()
        if isinstance(d, dict) and (d.get("InstrumentName") or d.get("instrumentName"))
    )
    print(f"封装批量函数: 返回 {len(batched)} 条, 有名称 {named2} 条, 耗时 {elapsed:.2f}s")
    if named2 <= 0:
        print("FAIL 封装批量函数未拿到名称")
        return False

    # 对比逐只（仅小样本，说明性能差异）
    t0 = time.time()
    one_by_one = 0
    for sym in sample:
        try:
            d = xtdata.get_instrument_detail(sym)
            if isinstance(d, dict) and d.get("InstrumentName"):
                one_by_one += 1
        except Exception:
            pass
    slow_elapsed = time.time() - t0
    print(f"逐只对比({len(sample)}只): 有名称 {one_by_one} 条, 耗时 {slow_elapsed:.2f}s")
    if slow_elapsed > 0 and elapsed > 0:
        print(f"样本提速约 {slow_elapsed / max(elapsed, 0.001):.1f}x")

    print("OK 批量路径可用")
    return True


def test_regenerate_csv() -> bool:
    from utils.stock_info_manager import StockInfoManager

    path = os.path.join(ROOT, "data", "all_a_stocks.csv")
    backup = path + ".bak_test"
    if os.path.isfile(path):
        import shutil
        shutil.copy2(path, backup)
        print(f"已备份: {backup}")

    mgr = StockInfoManager()
    mgr._stock_info_cache = None
    mgr._cache_time = None
    t0 = time.time()
    ok = mgr._create_stock_info_file(path)
    elapsed = time.time() - t0
    print(f"重建 all_a_stocks.csv: {'成功' if ok else '失败'}, 耗时 {elapsed:.1f}s")
    if ok and os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = sum(1 for _ in f) - 1
        print(f"文件行数(股票): {lines}")
    return bool(ok)


def main():
    parser = argparse.ArgumentParser(description="测试 stock_info 批量接口")
    parser.add_argument(
        "--full",
        action="store_true",
        help="在批量小样本通过后，重建 data/all_a_stocks.csv（较慢，会备份原文件）",
    )
    parser.add_argument("--sample", type=int, default=20, help="小样本数量，默认 20")
    args = parser.parse_args()

    print("=== stock_info 批量接口测试 ===")
    ok = test_batch_api(sample_size=max(5, args.sample))
    if not ok:
        sys.exit(1)
    if args.full:
        print("\n=== 全量重建 all_a_stocks.csv ===")
        if not test_regenerate_csv():
            sys.exit(2)
    else:
        print("\n若需验证全量生成，加参数: --full")


if __name__ == "__main__":
    main()
