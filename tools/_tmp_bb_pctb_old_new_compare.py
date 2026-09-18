# -*- coding: utf-8 -*-
"""Compare bb_pctb engine backtest: old (-0.012) vs new (-0.008+buffer5)."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

BASE = Path(r"d:/蚂蚁量化系统/history_data/布林%b回落选股")
OLD = BASE / "备份"
OUT = BASE / "_tmp_bb_pctb_old_new_compare.json"


def kind(rule: str) -> str:
    r = str(rule or "")
    if "止盈" in r:
        return "%b止盈"
    if "强清" in r or "FORCE" in r.upper() or "12日" in r:
        return "12日强清"
    if "斜率" in r:
        return "斜率止损"
    return "其他"


def load_pair(label: str, root: Path) -> dict:
    ret = pd.read_excel(root / "各日选股收益汇总_日线-bb_pctb-bb_pctb_sell-单点_按票_已完成_latest.xlsx")
    sell = pd.read_csv(root / "回测成交明细_日线-bb_pctb-bb_pctb_sell卖出_latest.csv", encoding="utf-8-sig")
    buy = pd.read_csv(root / "回测成交明细_日线-bb_pctb-bb_pctb_sell买入_latest.csv", encoding="utf-8-sig")

    r = pd.to_numeric(ret["收益率pct"], errors="coerce").dropna()
    sell["k"] = sell["规则名"].map(kind)
    # first sell per code+sel
    sell = sell.copy()
    sell["code"] = sell["代码"].astype(str).str.zfill(6)
    sell["sel"] = pd.to_datetime(sell["选股日"]).astype(str).str[:10]
    s1 = sell.sort_values("日期").groupby(["code", "sel"], as_index=False).first()

    exit_n = s1["k"].value_counts().to_dict()
    n = max(int(len(s1)), 1)
    exit_share = {k: round(v / n * 100, 1) for k, v in exit_n.items()}

    # attach ret to exit via merge with ret
    ret2 = ret.copy()
    ret2["code"] = ret2["代码"].astype(str).str.zfill(6)
    ret2["sel"] = pd.to_datetime(ret2["选股日"]).astype(str).str[:10]
    ret2["ret"] = pd.to_numeric(ret2["收益率pct"], errors="coerce")
    m = s1.merge(ret2[["code", "sel", "ret"]], on=["code", "sel"], how="left")
    by_exit = {}
    for k, g in m.groupby("k"):
        a = pd.to_numeric(g["ret"], errors="coerce").dropna()
        by_exit[k] = {
            "n": int(len(a)),
            "mean": round(float(a.mean()), 3) if len(a) else None,
            "win": round(float((a > 0).mean() * 100), 1) if len(a) else None,
            "median": round(float(a.median()), 3) if len(a) else None,
        }

    return {
        "label": label,
        "n_ret": int(len(ret)),
        "n_done": int(len(r)),
        "mean": round(float(r.mean()), 3),
        "median": round(float(r.median()), 3),
        "win": round(float((r > 0).mean() * 100), 1),
        "p25": round(float(r.quantile(0.25)), 3),
        "p75": round(float(r.quantile(0.75)), 3),
        "sum_ret_proxy": round(float(r.sum()), 1),
        "date_min": str(pd.to_datetime(ret["选股日"]).min().date()),
        "date_max": str(pd.to_datetime(ret["选股日"]).max().date()),
        "n_buy": int(len(buy)),
        "n_sell_rows": int(len(sell)),
        "n_sell_legs": int(len(s1)),
        "exit_n": {str(k): int(v) for k, v in exit_n.items()},
        "exit_share": exit_share,
        "by_exit": by_exit,
    }


def main() -> None:
    old = load_pair("旧：斜率-0.012 无缓冲", OLD)
    new = load_pair("新：斜率-0.008 缓冲5日", BASE)
    delta = {
        "mean_pp": round(new["mean"] - old["mean"], 3),
        "median_pp": round(new["median"] - old["median"], 3),
        "win_pp": round(new["win"] - old["win"], 1),
        "n_ret": new["n_ret"] - old["n_ret"],
    }
    # paired same keys
    o = pd.read_excel(OLD / "各日选股收益汇总_日线-bb_pctb-bb_pctb_sell-单点_按票_已完成_latest.xlsx")
    n = pd.read_excel(BASE / "各日选股收益汇总_日线-bb_pctb-bb_pctb_sell-单点_按票_已完成_latest.xlsx")
    for df in (o, n):
        df["code"] = df["代码"].astype(str).str.zfill(6)
        df["sel"] = pd.to_datetime(df["选股日"]).astype(str).str[:10]
        df["ret"] = pd.to_numeric(df["收益率pct"], errors="coerce")
    m = o[["code", "sel", "ret"]].merge(
        n[["code", "sel", "ret"]], on=["code", "sel"], suffixes=("_old", "_new")
    )
    m = m.dropna()
    m["d"] = m["ret_new"] - m["ret_old"]
    paired = {
        "n": int(len(m)),
        "mean_old": round(float(m["ret_old"].mean()), 3),
        "mean_new": round(float(m["ret_new"].mean()), 3),
        "mean_delta": round(float(m["d"].mean()), 3),
        "improved_pct": round(float((m["d"] > 1e-9).mean() * 100), 1),
        "worsened_pct": round(float((m["d"] < -1e-9).mean() * 100), 1),
        "unchanged_pct": round(float((m["d"].abs() <= 1e-9).mean() * 100), 1),
        "median_delta": round(float(m["d"].median()), 3),
    }
    out = {"old": old, "new": new, "delta": delta, "paired": paired}
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
