# -*- coding: utf-8 -*-
"""把马总选股逻辑1 结果写入买入策略股票池。

默认目标：买：马总逻辑1-涨停后跌破MA5/10/20各1/3
也可用 --name 写入单点版：买：马总逻辑1-涨停后跌破MA5/10/20各1/3-单点

- stock_codes = 文件内代码并集
- selection_date_by_code[code] = 该票最早选股日（多日出现取最早）
- 清空 _filled_legs

用法：
  python tools/load_ma_zong1_pool_into_strategy.py
  python tools/load_ma_zong1_pool_into_strategy.py path/to/选股结果_马总选股逻辑1_....xls
  python tools/load_ma_zong1_pool_into_strategy.py --name "买：马总逻辑1-涨停后跌破MA5/10/20各1/3-单点"
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
STRAT_DIR = ROOT / "strategy_generator_app" / "config" / "strategies"
DEFAULT_GLOB = ROOT / "history_data" / "马总选股逻辑" / "选股结果_马总选股逻辑1_*.xls"
DEFAULT_STRATEGY_NAME = "买：马总逻辑1-涨停后跌破MA5/10/20各1/3"


def _code6(v) -> str:
    s = str(v or "").strip()
    if not s or s.lower() == "nan":
        return ""
    m = re.match(r"^(\d+)\.0+$", s)
    if m:
        s = m.group(1)
    else:
        s = re.sub(r"[^\d]", "", s)
    if not s:
        return ""
    return s.zfill(6)[-6:]


def _find_strategy(strategy_name: str) -> Path:
    for p in STRAT_DIR.glob("strategy_*.json"):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if str(raw.get("name") or "") == strategy_name:
            return p
    raise SystemExit(
        "未找到策略：%s（弹性版: install_ma_zong1_ma_legs_strategy.py；"
        "单点版: install_ma_zong1_ma_legs_single_buy_strategy.py；"
        "独立腿: install_ma_zong1_single_ma_leg_strategies.py → 买：跌MA5/10/20）"
        % strategy_name
    )


def _pick_file(explicit: str) -> Path:
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise SystemExit("文件不存在: %s" % p)
        return p
    files = sorted(DEFAULT_GLOB.parent.glob(DEFAULT_GLOB.name), key=lambda x: x.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit("未找到 %s" % DEFAULT_GLOB)
    return files[0]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("xls", nargs="?", default="", help="选股结果 xls/xlsx")
    ap.add_argument(
        "--name",
        default=DEFAULT_STRATEGY_NAME,
        help="目标策略名（默认弹性版；单点版加 -单点 后缀）",
    )
    args = ap.parse_args()
    strategy_name = str(args.name or "").strip() or DEFAULT_STRATEGY_NAME
    xls = _pick_file(args.xls)
    print("load", xls)
    print("target", strategy_name)

    df = pd.read_excel(xls)
    date_col = None
    for c in df.columns:
        if "选股日" in str(c) or str(c).lower() == "screen_as_of":
            date_col = c
            break
    code_col = None
    for c in df.columns:
        if str(c) in ("股票代码", "代码", "code"):
            code_col = c
            break
    if date_col is None or code_col is None:
        raise SystemExit("需要列：选股日 + 股票代码，实际=%s" % list(df.columns)[:12])

    sel_map = {}
    for _, row in df.iterrows():
        c6 = _code6(row.get(code_col))
        if not c6:
            continue
        d = str(row.get(date_col) or "").strip()[:10]
        if len(d) < 10:
            continue
        prev = sel_map.get(c6)
        if prev is None or d < prev:
            sel_map[c6] = d

    codes = sorted(sel_map.keys())
    spath = _find_strategy(strategy_name)
    raw = json.loads(spath.read_text(encoding="utf-8"))
    raw["stock_codes"] = codes
    params = dict(raw.get("strategy_params") or {})
    params["selection_date_by_code"] = sel_map
    params["_filled_legs"] = []
    raw["strategy_params"] = params
    text = json.dumps(raw, ensure_ascii=False, indent=2) + "\n"
    spath.write_text(text, encoding="utf-8")
    print("strategy", spath.name)
    print("codes", len(codes), "date_range", min(sel_map.values()), "~", max(sel_map.values()))
    print("ok — 在策略生成器中刷新/重选该策略后设回测起止日 → 运行回测")
    print("提示：批量回测仿真长度=entry_window_trading_days（默认10）；")
    print("      「持有交易日数」写入参数供下一轮卖出，不拉长本轮买入仿真。")


if __name__ == "__main__":
    main()
