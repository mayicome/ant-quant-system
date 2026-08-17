# -*- coding: utf-8 -*-
from pathlib import Path

import pandas as pd

sell = pd.read_csv(
    Path(r"d:\蚂蚁量化系统\history_data\马总选股逻辑\回测成交明细_7-1-弹性买入-弹性卖出.csv"),
    encoding="utf-8-sig",
)


def code6(v) -> str:
    try:
        return f"{int(float(v)):06d}"
    except Exception:
        return str(v).strip().zfill(6)


sell["代码6"] = sell["代码"].map(code6)
op = sell[sell["规则名"].astype(str).str.contains("开盘涨幅", na=False)].copy()
op["数量"] = pd.to_numeric(op["数量"], errors="coerce").fillna(0).astype(int)
op["交易后持仓"] = pd.to_numeric(op["交易后持仓"], errors="coerce")

cnt = op.groupby("代码6").size()
multi = cnt[cnt >= 2].sort_values(ascending=False)
print(f"开盘半仓成交 ≥2 次的股票: {len(multi)}\n")

rows = []
for c, n in multi.items():
    g = op[op["代码6"] == c].sort_values(["日期", "时间"])
    name = g["股票名称"].iloc[0]
    detail = " | ".join(
        f"{r['日期']} {r['时间']}×{int(r['数量'])}" for _, r in g.iterrows()
    )
    rows.append(
        {
            "代码": c,
            "名称": name,
            "次数": int(n),
            "卖出总量": int(g["数量"].sum()),
            "明细": detail,
        }
    )

out = pd.DataFrame(rows)
print(out.to_string(index=False))

four = multi[multi >= 4]
if len(four):
    print("\n===== 次数≥4 完整流水 =====")
    for c in four.index:
        g = op[op["代码6"] == c].sort_values(["日期", "时间"])
        print(f"\n{c} {g['股票名称'].iloc[0]}")
        print(
            g[["日期", "时间", "价格", "数量", "交易后持仓", "腿键"]].to_string(
                index=False
            )
        )
