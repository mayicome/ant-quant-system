# -*- coding: utf-8 -*-
import sys
from collections import defaultdict
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.merge_backtest_trades_by_selection import (  # noqa: E402
    _code6_from_row,
    _int_vol,
    _parse_row_date,
    _read_rows,
)

DIR = Path(r"history_data/马总盘后新")
df = pd.read_excel(DIR / "各日选股收益汇总_612-828_全部单点_买入后持仓2日.xlsx", sheet_name="汇总")
sell_rows = _read_rows(DIR / "回测成交明细_612-828_全部单点卖出_买入后持仓2天.csv")

sell_vol = defaultdict(int)
sell_detail = defaultdict(list)
for r in sell_rows:
    if "卖" not in str(r.get("方向") or ""):
        continue
    c = _code6_from_row(r)
    d = _parse_row_date(r.get("日期"))
    v = _int_vol(r.get("数量"))
    if not c or not d or v <= 0:
        continue
    sell_vol[(c, d)] += v
    sell_detail[(c, d)].append((v, str(r.get("规则名") or ""), r.get("价格"), r.get("时间")))

sells_by_c = defaultdict(list)
for (cc, dd), v in sell_vol.items():
    sells_by_c[cc].append([dd, v])
for cc in sells_by_c:
    sells_by_c[cc].sort(key=lambda x: x[0])

rows = []
for _, r in df.iterrows():
    c = str(r.get("代码") or "")
    digits = "".join(ch for ch in c if ch.isdigit())
    c6 = digits.zfill(6)[-6:] if digits else ""
    bd = pd.to_datetime(r["买入日"], errors="coerce")
    ed = pd.to_datetime(r["计划持仓结束日"], errors="coerce")
    bq = int(float(r.get("买入数量合计") or 0) or 0)
    if not c6 or pd.isna(bd) or pd.isna(ed) or bq <= 0:
        continue
    rows.append(
        {
            "c": c6,
            "name": r.get("股票名称"),
            "bd": bd.date(),
            "ed": ed.date(),
            "bq": bq,
            "ret": r.get("收益率pct"),
            "sq": int(float(r.get("卖出数量合计") or 0) or 0),
        }
    )
rows.sort(key=lambda x: (x["c"], x["bd"]))

sells_work = {c: [[d, v] for d, v in lst] for c, lst in sells_by_c.items()}
ptr = defaultdict(int)
late_rows = []
for row in rows:
    c = row["c"]
    need = row["bq"]
    ed = row["ed"]
    bd = row["bd"]
    got_in = 0
    got_late = 0
    late_dates = []
    in_dates = []
    lst = sells_work.get(c) or []
    i = ptr[c]
    while need > 0 and i < len(lst):
        dd, left = lst[i]
        if left <= 0:
            i += 1
            continue
        if dd < bd:
            i += 1
            continue
        take = min(need, left)
        lst[i][1] -= take
        need -= take
        if dd <= ed:
            got_in += take
            in_dates.append((str(dd), take))
        else:
            got_late += take
            late_dates.append((str(dd), take))
        if lst[i][1] <= 0:
            i += 1
    ptr[c] = i
    if need == 0 and got_late > 0:
        late_rules = []
        for ds, _tv in late_dates:
            y, m, d = map(int, ds.split("-"))
            from datetime import date as _date

            dd = _date(y, m, d)
            for v, rule, px, tm in sell_detail.get((c, dd), []):
                late_rules.append(f"{ds} {tm or ''} {rule} {v}股 @{px}".strip())
        late_rows.append(
            {
                **row,
                "got_in": got_in,
                "got_late": got_late,
                "in_dates": in_dates,
                "late_dates": late_dates,
                "late_rules": late_rules[:6],
            }
        )

all_late = [x for x in late_rows if x["got_in"] == 0]
half = [x for x in late_rows if x["got_in"] > 0 and x["got_late"] > 0]


def late_span(x):
    if not x["late_dates"]:
        return 0
    return (pd.Timestamp(x["late_dates"][-1][0]) - pd.Timestamp(str(x["ed"]))).days


all_late.sort(key=late_span, reverse=True)
half.sort(key=lambda x: x["got_late"] / max(1, x["bq"]), reverse=True)

print(f"窗外清仓合计 {len(late_rows)}；全在结束后卖 {len(all_late)}；部分窗外 {len(half)}")
print()
print("=== 典型A：计划结束日前一股没卖，全部结束后才卖 ===")
for x in all_late[:3]:
    print(
        f"{x['c']} {x['name']} 买{x['bd']} 计划结束{x['ed']} "
        f"买{x['bq']}股 汇总收益{x['ret']}%"
    )
    print(f"  窗内卖出 {x['got_in']} / 结束后卖出 {x['got_late']}")
    for line in x["late_rules"][:4]:
        print(" ", line)
    print()
print("=== 典型B：窗内卖一部分，结束后又卖 ===")
for x in half[:3]:
    print(
        f"{x['c']} {x['name']} 买{x['bd']} 计划结束{x['ed']} "
        f"买{x['bq']}股 汇总收益{x['ret']}%"
    )
    print(f"  窗内: {x['in_dates']} 合计{x['got_in']}")
    print(f"  窗外: {x['late_dates']} 合计{x['got_late']}")
    for line in x["late_rules"][:4]:
        print(" ", line)
    print()
