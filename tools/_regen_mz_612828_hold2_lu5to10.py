# -*- coding: utf-8 -*-
"""重生成 各日选股收益汇总_612-828_近10to5日_持仓2日.xlsx（持仓2日=买入次日起算第2日结束）。"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DIR = ROOT / "history_data" / "马总盘后新"

from tools.summarize_hold_days_from_trades import (  # noqa: E402
    filter_sells_within_hold,
    summarize_one,
)
from tools.merge_backtest_trades_by_selection import _read_rows  # noqa: E402


HOLD = 2
LU_LO, LU_HI = 5, 10


def _lu_day(r: dict) -> Optional[int]:
    v = r.get("选股日为涨停后第几日")
    try:
        if v is not None and str(v).strip() not in ("", "None", "nan"):
            return int(float(v))
    except (TypeError, ValueError):
        pass
    # 选股结果里通常是「最近的涨停板是几日前」；当日涨停时常为空 → 记 0
    v2 = r.get("最近的涨停板是几日前")
    try:
        if v2 is not None and str(v2).strip() not in ("", "None", "nan"):
            return int(float(v2))
    except (TypeError, ValueError):
        pass
    ct = r.get("条件_当日涨停")
    if ct in (True, "True", "true", 1, "1", "是"):
        return 0
    return None


def _fill_lu_day(rows: List[dict]) -> None:
    for r in rows:
        d = _lu_day(r)
        if d is not None:
            r["选股日为涨停后第几日"] = int(d)


def _stats(rows: List[dict], scope: str) -> Dict[str, Any]:
    buys = []
    rets = []
    buy_amts = []
    buy_ns = []
    for r in rows:
        ba = float(r.get("买入金额合计") or 0)
        if ba <= 0:
            continue
        buys.append(r)
        buy_amts.append(ba)
        buy_ns.append(int(float(r.get("买入笔数") or 0) or 0))
        try:
            rets.append(float(r.get("收益率pct") or 0))
        except (TypeError, ValueError):
            rets.append(0.0)
    n = len(buys)
    total_buy = sum(buy_amts)
    pnl = sum(a * rp / 100.0 for a, rp in zip(buy_amts, rets))
    buy_ds = sorted(
        {
            str(r.get("买入日") or "").strip()[:10]
            for r in buys
            if str(r.get("买入日") or "").strip()
        }
    )
    return {
        "范围": scope,
        "仓位数": n,
        "买入笔数合计": int(sum(buy_ns)),
        "均收益率pct": round(sum(rets) / n, 4) if n else None,
        "中位数pct": round(sorted(rets)[n // 2], 4) if n else None,
        "胜率pct": round(100.0 * sum(1 for x in rets if x > 0) / n, 2) if n else None,
        "金额加权收益pct": round(100.0 * pnl / total_buy, 4) if total_buy else None,
        "买入金额合计": round(total_buy, 2) if total_buy else 0,
        "买入日起": buy_ds[0] if buy_ds else "",
        "买入日止": buy_ds[-1] if buy_ds else "",
    }


def _df(rows: List[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    front = [
        "选股日",
        "选股日为涨停后第几日",
        "首次选股日",
        "代码",
        "股票名称",
        "买入日",
        "持有交易日数",
        "计划持仓结束日",
        "end_date",
        "盯市日期",
        "盯市类型",
    ]
    cols = list(df.columns)
    ordered = [c for c in front if c in cols] + [c for c in cols if c not in front]
    return df[ordered] if ordered else df


def main() -> int:
    buy10 = DIR / "回测成交明细_612-828_买入.csv"
    buy4 = DIR / "回测成交明细_612-828_买入_近4日.csv"
    sell_p = DIR / "回测成交明细_612-828_卖出._持有2天.csv"
    if not sell_p.is_file():
        sell_p = DIR / "回测成交明细_612-828_卖出.csv"
    sel = DIR / "选股结果_马总选股逻辑-盘后_2026-06-12_2026-08-28.xls"
    out_p = DIR / "各日选股收益汇总_612-828_近10to5日_持仓2日.xlsx"

    sell_all = _read_rows(sell_p)
    sel_path = sel if sel.is_file() else None

    print(f"[1/3] 近10日 hold={HOLD} …")
    filt10, st10 = filter_sells_within_hold(_read_rows(buy10), sell_all, HOLD)
    print(f"  sells kept={st10}")
    rows10, meta10 = summarize_one(buy10, filt10, HOLD, selection_path=sel_path)
    _fill_lu_day(rows10)
    print(f"  meta={meta10}")

    print(f"[2/3] 近4日 hold={HOLD} …")
    filt4, st4 = filter_sells_within_hold(_read_rows(buy4), sell_all, HOLD)
    print(f"  sells kept={st4}")
    rows4, meta4 = summarize_one(buy4, filt4, HOLD, selection_path=sel_path)
    _fill_lu_day(rows4)
    print(f"  meta={meta4}")

    # 校验持有交易日数 / 结束日样例
    sample = [r for r in rows10 if float(r.get("买入金额合计") or 0) > 0][:5]
    for r in sample:
        print(
            "  sample",
            r.get("买入日"),
            "hold",
            r.get("持有交易日数"),
            "end",
            r.get("计划持仓结束日"),
        )

    subset = [
        r
        for r in rows10
        if float(r.get("买入金额合计") or 0) > 0
        and (_lu_day(r) is not None)
        and LU_LO <= int(_lu_day(r)) <= LU_HI
    ]
    by_day: Dict[int, List[dict]] = {d: [] for d in range(LU_LO, LU_HI + 1)}
    for r in subset:
        by_day[int(_lu_day(r))].append(r)

    overview = [
        _stats(subset, f"近10日选股∩涨停后第{LU_LO}～{LU_HI}日（持仓{HOLD}日)"),
    ]
    for d in range(LU_LO, LU_HI + 1):
        overview.append(_stats(by_day[d], f"涨停后第{d}日"))
    overview.append(_stats(rows10, f"参考：近10日全体（持仓{HOLD}）"))
    overview.append(_stats(rows4, f"参考：近4日全体（持仓{HOLD}）"))

    with pd.ExcelWriter(out_p, engine="openpyxl") as w:
        pd.DataFrame(overview).to_excel(w, sheet_name="对照总览", index=False)
        _df(subset).to_excel(w, sheet_name="近5to10日明细", index=False)
        for d in range(LU_LO, LU_HI + 1):
            _df(by_day[d]).to_excel(w, sheet_name=f"第{d}日", index=False)

    print(f"[3/3] wrote {out_p}")
    print(pd.DataFrame(overview).to_string(index=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
