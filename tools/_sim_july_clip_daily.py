# -*- coding: utf-8 -*-
"""Day-by-day compare C+pullback clip(2,4) vs clip(2,6) at 100k."""
import importlib.util
from pathlib import Path

import pandas as pd

spec = importlib.util.spec_from_file_location(
    "sim", Path(r"d:\蚂蚁量化系统\tools\_sim_july_clip_100k.py")
)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

r4 = m.sim(m.C_both, 2, 4, 100_000, "clip(2,4)")
r6 = m.sim(m.C_both, 2, 6, 100_000, "clip(2,6)")
all_sel = sorted(d for d in m.Smap if d.startswith("2026-07"))


def day_table(r):
    f = r["fills"].copy()
    s = r["skips"].copy() if len(r["skips"]) else pd.DataFrame()
    rows = []
    for d in all_sel:
        S = m.Smap[d]
        Seff = m.clip_n(S, r["L"], r["U"])
        fd = f[f["选股日"] == d] if len(f) else f
        n = len(fd)
        n_o = int((fd["分支"] == "开盘夹档").sum()) if n else 0
        n_p = int((fd["分支"] == "高开回踩").sum()) if n else 0
        spend = float(fd["spend"].sum()) if n else 0.0
        pnl = float(fd["pnl"].sum()) if n else 0.0
        day_ret = pnl / spend * 100 if spend > 0 else 0.0
        n_skip = int((s["选股日"] == d).sum()) if len(s) else 0

        def fmt_row(row, tag_open="开", tag_pb="回"):
            tag = tag_open if row["分支"] == "开盘夹档" else tag_pb
            return f"{row['代码']}({tag}{row['ret_pct']:+.1f}%)"

        codes = ",".join(fmt_row(row) for _, row in fd.iterrows()) if n else ""
        skip_codes = ""
        if n_skip:
            ss = s[s["选股日"] == d]
            skip_codes = ",".join(
                f"{row['代码']}({'开' if row['分支']=='开盘夹档' else '回'})"
                for _, row in ss.iterrows()
            )
        rows.append(
            dict(
                选股日=d,
                S=S,
                Seff=Seff,
                成交=n,
                开=n_o,
                回=n_p,
                跳过=n_skip,
                花费=spend,
                盈亏=pnl,
                日收益pct=day_ret,
                明细=codes,
                跳过明细=skip_codes,
            )
        )
    return pd.DataFrame(rows)


t4 = day_table(r4)
t6 = day_table(r6)

cmp = t4[["选股日", "S"]].copy()
cmp["Seff4"] = t4["Seff"]
cmp["Seff6"] = t6["Seff"]
cmp["成交4"] = t4["成交"].astype(str) + "(开" + t4["开"].astype(str) + "回" + t4["回"].astype(str) + ")"
cmp["成交6"] = t6["成交"].astype(str) + "(开" + t6["开"].astype(str) + "回" + t6["回"].astype(str) + ")"
cmp["跳4"] = t4["跳过"]
cmp["跳6"] = t6["跳过"]
cmp["日收益4%"] = t4["日收益pct"].round(2)
cmp["日收益6%"] = t6["日收益pct"].round(2)
cmp["盈亏4"] = t4["盈亏"].round(0)
cmp["盈亏6"] = t6["盈亏"].round(0)
cmp["Δ盈亏"] = (t6["盈亏"] - t4["盈亏"]).round(0)

print("===== 按选股日对比  C开盘+回踩  10万  clip(2,4) vs clip(2,6) =====")
print(cmp.to_string(index=False))
print()
print(
    f"合计盈亏4: {t4['盈亏'].sum():+.0f}   合计盈亏6: {t6['盈亏'].sum():+.0f}   "
    f"差(6-4): {t6['盈亏'].sum()-t4['盈亏'].sum():+.0f}"
)
print(f"期末4: {r4['final']:.0f} ({r4['ret']:+.2f}%)   期末6: {r6['final']:.0f} ({r6['ret']:+.2f}%)")

print("\n===== clip(2,4) 有交易日明细 =====")
for _, row in t4.iterrows():
    if row["成交"] == 0 and row["跳过"] == 0:
        continue
    print(
        f"{row['选股日']} S={row['S']} Seff={row['Seff']} "
        f"成交{row['成交']}(开{row['开']}回{row['回']}) 跳{row['跳过']} "
        f"花费{row['花费']:.0f} 盈亏{row['盈亏']:+.0f} 日收益{row['日收益pct']:+.2f}%"
    )
    if row["明细"]:
        print(f"  买: {row['明细']}")
    if row["跳过明细"]:
        print(f"  跳: {row['跳过明细']}")

print("\n===== clip(2,6) 有交易日明细 =====")
for _, row in t6.iterrows():
    if row["成交"] == 0 and row["跳过"] == 0:
        continue
    print(
        f"{row['选股日']} S={row['S']} Seff={row['Seff']} "
        f"成交{row['成交']}(开{row['开']}回{row['回']}) 跳{row['跳过']} "
        f"花费{row['花费']:.0f} 盈亏{row['盈亏']:+.0f} 日收益{row['日收益pct']:+.2f}%"
    )
    if row["明细"]:
        print(f"  买: {row['明细']}")
    if row["跳过明细"]:
        print(f"  跳: {row['跳过明细']}")

# highlight days where U=6 differs in fills or pnl materially
print("\n===== 差异主要来自哪些选股日 (Δ盈亏≠0) =====")
diff = cmp[cmp["Δ盈亏"] != 0][
    ["选股日", "S", "Seff4", "Seff6", "成交4", "成交6", "跳4", "跳6", "日收益4%", "日收益6%", "盈亏4", "盈亏6", "Δ盈亏"]
]
print(diff.to_string(index=False))
