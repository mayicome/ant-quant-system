# -*- coding: utf-8 -*-
import warnings

warnings.filterwarnings("ignore")
import json
from pathlib import Path

import numpy as np
import pandas as pd

src = Path(
    r"history_data/马总选股逻辑/各日选股收益汇总_日线-ma10-单点_按票_20260815_100317_收盘上MA10.xlsx"
)
df = pd.read_excel(src)
df["sel"] = pd.to_datetime(df["选股日"]).dt.strftime("%Y-%m-%d")
df["ret"] = pd.to_numeric(df["收益率pct"], errors="coerce")
df["buy_amt"] = pd.to_numeric(df["买入金额合计"], errors="coerce")
df["mx"] = pd.to_numeric(df["前十个交易日最高涨幅"], errors="coerce")
df["thr"] = pd.to_numeric(df["前10日大涨阈值"], errors="coerce")


def code6(v):
    s = str(v or "").strip()
    if s.endswith(".0"):
        s = s[:-2]
    digits = "".join(c for c in s if c.isdigit())
    return digits.zfill(6)[-6:] if digits else ""


df["c6"] = df["代码"].map(code6)
df["is20"] = df["c6"].str.startswith(("300", "301", "688", "689"))
# normalize
df["mx_n"] = np.where(df["is20"], df["mx"] / 2.0, df["mx"])
df = df[df["ret"].notna() & df["mx_n"].notna()].copy()
df["ret_cs"] = df["ret"] - df.groupby("sel")["ret"].transform("mean")

print("n", len(df), "is20", int(df["is20"].sum()))
print("agree thr==10", float(((df["is20"]) == (df["thr"] == 10)).mean()))
print("corr raw", round(float(df["mx"].corr(df["ret"])), 4))
print("corr norm", round(float(df["mx_n"].corr(df["ret"])), 4))
print("corr_cs norm", round(float(df["mx_n"].corr(df["ret_cs"])), 4))
try:
    from scipy.stats import spearmanr

    print("spearman norm", round(float(spearmanr(df["mx_n"], df["ret"]).correlation), 4))
except Exception:
    pass

print("\nmx_n describe", df["mx_n"].describe(percentiles=[0.1, 0.25, 0.5, 0.75, 0.9]).round(3).to_string())

print("\n=== 五分位 ===")
df["q"] = pd.qcut(df["mx_n"], 5, duplicates="drop")
rows = []
for i, (lab, g) in enumerate(df.groupby("q", observed=True)):
    r, cs = g["ret"], g["ret_cs"]
    w = np.average(r, weights=g["buy_amt"]) if g["buy_amt"].sum() > 0 else np.nan
    print(
        f"Q{i+1} {lab}: n={len(g):4d} mean={r.mean():+.3f} med={r.median():+.3f} "
        f"win={(r > 0).mean() * 100:5.1f}% wret={w:+.3f} cs={cs.mean():+.3f} "
        f"is20={int(g['is20'].sum())}"
    )
    rows.append(
        {
            "q": i + 1,
            "n": int(len(g)),
            "mean": round(float(r.mean()), 3),
            "med": round(float(r.median()), 3),
            "win": round(float((r > 0).mean() * 100), 1),
            "cs": round(float(cs.mean()), 3),
            "wret": round(float(w), 3),
            "lo": round(float(g["mx_n"].min()), 3),
            "hi": round(float(g["mx_n"].max()), 3),
            "is20": int(g["is20"].sum()),
        }
    )

print("\n=== 固定带 ===")
bands = [
    ("归一化<3%", df["mx_n"] < 3),
    ("3–5%", (df["mx_n"] >= 3) & (df["mx_n"] < 5)),
    ("5–8%", (df["mx_n"] >= 5) & (df["mx_n"] < 8)),
    ("8–9.9%", (df["mx_n"] >= 8) & (df["mx_n"] < 9.9)),
    ("≈涨停(9.9–10.1)", (df["mx_n"] >= 9.9) & (df["mx_n"] <= 10.1)),
    (">10.1%", df["mx_n"] > 10.1),
]
brows = []
for lab, m in bands:
    g = df[m]
    if len(g) == 0:
        continue
    r, cs = g["ret"], g["ret_cs"]
    print(
        f"{lab}: n={len(g):4d} mean={r.mean():+.3f} med={r.median():+.3f} "
        f"win={(r > 0).mean() * 100:5.1f}% cs={cs.mean():+.3f}"
    )
    brows.append(
        {
            "lab": lab,
            "n": int(len(g)),
            "mean": round(float(r.mean()), 3),
            "med": round(float(r.median()), 3),
            "win": round(float((r > 0).mean() * 100), 1),
            "cs": round(float(cs.mean()), 3),
        }
    )

# also condition 前10日无大涨
print("\n=== 条件_前10日无大涨 ===")
ok = df["条件_前10日无大涨"].map(lambda x: bool(x) if pd.notna(x) else False)
for lab, m in [("无大涨=True", ok), ("无大涨=False", ~ok)]:
    g = df[m]
    r, cs = g["ret"], g["ret_cs"]
    print(
        f"{lab}: n={len(g):4d} mean={r.mean():+.3f} med={r.median():+.3f} "
        f"win={(r > 0).mean() * 100:5.1f}% cs={cs.mean():+.3f}"
    )

out = {
    "n": int(len(df)),
    "n20": int(df["is20"].sum()),
    "corr_raw": round(float(df["mx"].corr(df["ret"])), 4),
    "corr_norm": round(float(df["mx_n"].corr(df["ret"])), 4),
    "corr_cs": round(float(df["mx_n"].corr(df["ret_cs"])), 4),
    "quintiles": rows,
    "bands": brows,
}
Path(r"C:\Users\Administrator\.cursor\projects\d\agent-tools\ma10_prior_max_norm.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
)
print("wrote")
