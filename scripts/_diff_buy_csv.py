# -*- coding: utf-8 -*-
import pandas as pd
from pathlib import Path

def load(p):
    df = pd.read_csv(p, encoding="utf-8")
    df["code6"] = df["代码"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(6)
    df["key"] = df["选股日"].astype(str) + "|" + df["code6"]
    return df

ROOT = Path(r"d:/蚂蚁量化系统/history_data")
files = {
    "新回测_53": ROOT / "新回测/回测成交明细_买入_判断真突破不开探测新_3-5月.csv",
    "新回测_50": ROOT / "新回测/回测成交明细_买入_判断真突破不开探测_3-5月.csv",
    "回测五月_50": ROOT / "回测五月/回测成交明细_买入_判断真突破不开探测_3-5月.csv",
    "回测五月_重复129": ROOT / "回测五月/回测成交明细_买入_判断真突破不开探测_重复判断_3-5月.csv",
}

data = {k: load(p) for k, p in files.items() if p.exists()}
for k, df in data.items():
    print(k, len(df))

new = data["新回测_53"]
old = data["回测五月_50"]
only_new = sorted(set(new["key"]) - set(old["key"]))
only_old = sorted(set(old["key"]) - set(new["key"]))
print("\n=== 新回测53 多出 ===")
for k in only_new:
    r = new[new["key"] == k].iloc[0]
    print(f"  {r['选股日']} {r['代码']} {r['股票名称']} | 买入 {r['日期']} {r['时间']} | {r['价格']} x {r['数量']}")
print("\n=== 回测五月50 多出 ===")
for k in only_old:
    r = old[old["key"] == k].iloc[0]
    print(f"  {r['选股日']} {r['代码']} {r['股票名称']} | 买入 {r['日期']} {r['时间']} | {r['价格']} x {r['数量']}")

common = sorted(set(new["key"]) & set(old["key"]))
print(f"\n=== 共有 {len(common)} 条 ===")
vol_diff = []
for k in common:
    a = new[new["key"] == k].iloc[0]
    b = old[old["key"] == k].iloc[0]
    if int(a["数量"]) != int(b["数量"]):
        vol_diff.append((k, a["股票名称"], int(b["数量"]), int(a["数量"])))
print("数量不同:", len(vol_diff))
for row in vol_diff[:5]:
    print(" ", row)

# compare with 129 repeat file
rep = data.get("回测五月_重复129")
if rep is not None:
    only_rep = sorted(set(rep["key"]) - set(old["key"]))
    print(f"\n=== 回测五月重复129 比50多 {len(only_rep)} 条 ===")
    for k in only_rep[:20]:
        r = rep[rep["key"] == k].iloc[0]
        print(f"  {r['选股日']} {r['代码']} {r['股票名称']} | {r['日期']} {r['时间']}")
