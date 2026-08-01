# -*- coding: utf-8 -*-
import pandas as pd
from pathlib import Path

ROOT = Path(r"d:/蚂蚁量化系统/history_data")


def load(name, sub):
    p = ROOT / sub / name
    df = pd.read_csv(p, encoding="utf-8")
    df["code6"] = df["代码"].astype(str).str.replace(r"\D", "", regex=True).str.zfill(6)
    df["sel_key"] = df["选股日"].astype(str) + "|" + df["code6"]
    df["fill_key"] = (
        df["日期"].astype(str)
        + "|"
        + df["时间"].astype(str)
        + "|"
        + df["sel_key"]
    )
    return df, p


pairs = [
    (
        "回测成交明细_买入_判断真突破不开探测新_3-5月.csv",
        "新回测",
        "回测成交明细_买入_判断真突破不开探测_3-5月.csv",
        "回测五月",
        "新53 vs 五月50",
    ),
    (
        "回测成交明细_买入_判断真突破不开探测新_3-5月.csv",
        "新回测",
        "回测成交明细_买入_判断真突破不开探测_重复判断_3-5月.csv",
        "回测五月",
        "新53 vs 五月重复129",
    ),
]

for new_name, new_sub, old_name, old_sub, label in pairs:
    new, np = load(new_name, new_sub)
    old, op = load(old_name, old_sub)
    print("=" * 70)
    print(label)
    print("  new:", np.name, len(new))
    print("  old:", op.name, len(old))

    only_new = sorted(set(new["sel_key"]) - set(old["sel_key"]))
    only_old = sorted(set(old["sel_key"]) - set(new["sel_key"]))
    common = sorted(set(new["sel_key"]) & set(old["sel_key"]))
    print(f"  only new: {len(only_new)}  only old: {len(only_old)}  common: {len(common)}")

    if only_new:
        print("  --- only in new ---")
        for k in only_new:
            r = new[new["sel_key"] == k].iloc[0]
            print(
                f"    {r['选股日']} {r['code6']} {r['股票名称']} "
                f"fill={r['日期']} {r['时间']} px={r['价格']} vol={r['数量']}"
            )

    time_diff = []
    px_diff = []
    vol_diff = []
    trig_diff = []
    for k in common:
        a = new[new["sel_key"] == k].iloc[0]
        b = old[old["sel_key"] == k].iloc[0]
        if str(a["日期"]) != str(b["日期"]) or str(a["时间"]) != str(b["时间"]):
            time_diff.append(
                (
                    k,
                    a["股票名称"],
                    f"{b['日期']} {b['时间']}",
                    f"{a['日期']} {a['时间']}",
                    int(b["数量"]),
                    int(a["数量"]),
                )
            )
        if float(a["价格"]) != float(b["价格"]):
            px_diff.append((k, a["股票名称"], b["价格"], a["价格"]))
        if int(a["数量"]) != int(b["数量"]):
            vol_diff.append((k, a["股票名称"], int(b["数量"]), int(a["数量"])))
        ta = (a.get("触发信息") or "")[:80]
        tb = (b.get("触发信息") or "")[:80]
        if ta != tb:
            trig_diff.append((k, a["股票名称"]))

    print(f"  common with DIFFERENT fill time: {len(time_diff)}")
    for row in time_diff:
        print(f"    {row[0]} {row[1]}")
        print(f"      old: {row[2]} vol={row[4]}")
        print(f"      new: {row[3]} vol={row[5]}")

    print(f"  common with different price: {len(px_diff)}")
    for row in px_diff[:10]:
        print(f"    {row}")

    print(f"  common with different volume: {len(vol_diff)}")
    if vol_diff:
        ratios = sorted({round(r[3] / r[2], 3) for r in vol_diff if r[2]})
        print(f"    volume ratios new/old sample: {ratios[:8]}")

    print(f"  common with different trigger text: {len(trig_diff)}")
    print()
