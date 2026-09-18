# -*- coding: utf-8 -*-
"""全部单点：截断持仓2日窗外卖出，剩余按结束日盯市，重写汇总表。"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.merge_backtest_trades_by_selection import _read_rows  # noqa: E402
from tools.summarize_hold_days_from_trades import (  # noqa: E402
    filter_sells_within_hold,
    summarize_one,
)

DIR = ROOT / "history_data" / "马总盘后新"
BUY = DIR / "回测成交明细_612-828_全部单点买入.csv"
SELL = DIR / "回测成交明细_612-828_全部单点卖出_买入后持仓2天.csv"
SEL = DIR / "选股结果_马总选股逻辑-盘后_2026-06-12_2026-08-28.xls"
OUT = DIR / "各日选股收益汇总_612-828_全部单点_买入后持仓2日.xlsx"
HOLD = 2


def main() -> int:
    buy_rows = _read_rows(BUY)
    sell_rows = _read_rows(SELL)
    print("buy", len(buy_rows), "sell", len(sell_rows))
    filt, st = filter_sells_within_hold(buy_rows, sell_rows, HOLD)
    print("filter_sells", st)
    rows, meta = summarize_one(
        BUY,
        filt,
        HOLD,
        selection_path=SEL if SEL.is_file() else None,
    )
    print("meta", meta)

    df = pd.DataFrame(rows)
    front = [
        "选股日",
        "首次选股日",
        "代码",
        "股票名称",
        "买入日",
        "末笔买入日",
        "持有交易日数",
        "计划持仓结束日",
        "end_date",
        "盯市日期",
        "盯市类型",
        "买入时间",
        "买入笔数",
        "卖出笔数",
        "买入金额合计",
        "卖出金额合计",
        "买入数量合计",
        "卖出数量合计",
        "剩余持仓数量",
        "净现金流_卖减买",
        "收盘价",
        "剩余市值_盯市",
        "收益率pct",
        "备注",
    ]
    cols = list(df.columns)
    ordered = [c for c in front if c in cols] + [c for c in cols if c not in front]
    df = df[ordered]

    print("盯市类型", df["盯市类型"].value_counts(dropna=False).to_dict())
    rem = pd.to_numeric(df["剩余持仓数量"], errors="coerce").fillna(0)
    print("剩余>0", int((rem > 0).sum()), "剩余=0", int((rem <= 0).sum()))

    # 抽样：庄园牧场 / 交大昂立相关
    for key in ("2910", "庄园", "603407"):
        m = df[
            df["代码"].astype(str).str.contains(key, na=False)
            | df["股票名称"].astype(str).str.contains(key, na=False)
        ]
        if len(m):
            cols2 = [
                c
                for c in [
                    "代码",
                    "股票名称",
                    "买入日",
                    "末笔买入日",
                    "计划持仓结束日",
                    "盯市日期",
                    "盯市类型",
                    "买入数量合计",
                    "卖出数量合计",
                    "剩余持仓数量",
                    "收益率pct",
                    "备注",
                ]
                if c in df.columns
            ]
            print(m[cols2].to_string(index=False))

    out_tmp = OUT.with_suffix(".xlsx.tmp")
    with pd.ExcelWriter(out_tmp, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="汇总", index=False)
    try:
        out_tmp.replace(OUT)
        wrote = OUT
    except OSError:
        alt = OUT.with_name(OUT.stem + "_已剔到期后腿.xlsx")
        try:
            out_tmp.replace(alt)
        except OSError:
            import shutil

            shutil.copy2(out_tmp, alt)
            out_tmp.unlink(missing_ok=True)
        wrote = alt
        print("WARN: 原文件被占用，已写到", wrote)
    print("wrote", wrote, "rows", len(df))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
