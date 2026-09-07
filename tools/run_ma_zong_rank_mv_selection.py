# -*- coding: utf-8 -*-
"""马总次日MA10 · 排名市值选股（独立程序）

硬过滤 = 次日MA10 原硬条件 + 强制：
  最佳板块排名 ∈ [5,20]  ∧  流通市值 < 50 亿（优先选股日市值）

规则名：马总选股逻辑-次日MA10-排名市值
安装：python tools/install_ma_zong_next_day_ma10_rank_mv_rule.py
结果：history_data/马总选股逻辑/排名市值/选股结果_*.xls
      （同时落一份到 马总选股逻辑/ 根目录，便于其它工具发现）

用法:
  python tools/run_ma_zong_rank_mv_selection.py
  python tools/run_ma_zong_rank_mv_selection.py --days 5
  python tools/run_ma_zong_rank_mv_selection.py --start 2026-09-01 --end 2026-09-05
"""
from __future__ import annotations

import argparse
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

RULE_NAME = "马总选股逻辑-次日MA10-排名市值"
OUT_DIR = ROOT / "history_data" / "马总选股逻辑" / "排名市值"
PARENT_DIR = ROOT / "history_data" / "马总选股逻辑"


def _parse_d(s: str) -> date:
    return date.fromisoformat(str(s).strip()[:10])


def _last_closed() -> date:
    try:
        from em_hot_clip_monitor import last_closed_trading_day

        return last_closed_trading_day()
    except Exception:
        return date.today() - timedelta(days=1)


def _trading_window(end: date, days: int) -> tuple[date, date]:
    """取 end 往前约 days 个自然日窗口（选股线程内按交易日扫）。"""
    days = max(1, int(days))
    start = end - timedelta(days=max(days * 2, days + 7))
    return start, end


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="马总次日MA10-排名市值 选股")
    ap.add_argument("--days", type=int, default=1, help="最近 N 个自然日窗口（默认1=最近已收盘日）")
    ap.add_argument("--start", type=str, default="", help="选股起 YYYY-MM-DD")
    ap.add_argument("--end", type=str, default="", help="选股止 YYYY-MM-DD，默认最近已收盘日")
    ap.add_argument(
        "--reinstall",
        action="store_true",
        help="先重装规则再选股",
    )
    args = ap.parse_args(argv)

    if args.reinstall:
        from install_ma_zong_next_day_ma10_rank_mv_rule import main as install_main

        install_main()

    end = _parse_d(args.end) if args.end else _last_closed()
    if args.start:
        start = _parse_d(args.start)
    elif int(args.days) <= 1:
        start = end
    else:
        start, end = _trading_window(end, int(args.days))

    if start > end:
        print(f"日期无效: {start} > {end}", file=sys.stderr)
        return 2

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PARENT_DIR.mkdir(parents=True, exist_ok=True)

    import prepare_ma10_regime_data as prep

    print(f"规则: {RULE_NAME}")
    print(f"区间: {start} → {end}")
    print(f"输出: {OUT_DIR}")

    # prepare 默认写入 马总选股逻辑/；选完再拷到 排名市值/
    path = prep.run_selection(start, end, rule_name=RULE_NAME)
    path = Path(path)
    if not path.is_file():
        print(f"选股未产出文件: {path}", file=sys.stderr)
        return 1

    dest = OUT_DIR / path.name
    shutil.copy2(path, dest)
    latest = OUT_DIR / f"选股结果_{RULE_NAME}_latest.xls"
    try:
        if latest.exists() or latest.is_symlink():
            latest.unlink()
    except OSError:
        pass
    shutil.copy2(path, latest)

    print(f"完成: {path}")
    print(f"副本: {dest}")
    print(f"latest: {latest}")
    print("选股系统里也可勾选启用「马总选股逻辑-次日MA10-排名市值」直接跑。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
