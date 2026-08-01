# -*- coding: utf-8 -*-
"""日线开盘买入回测 · 叠加过滤条件对照分析。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "daily_cache"
SRC = ROOT / "history_data" / "主力净流入回测" / "各日选股收益汇总_基于日线_6月9日前.xlsx"
OUT_XLSX = ROOT / "history_data" / "主力净流入回测" / "各日选股收益汇总_基于日线_6月9日前_过滤对照.xlsx"
OUT_JSON = ROOT / "history_data" / "主力净流入回测" / "_daily_open_buy_filters.json"


def _norm_code(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def _full_code(code6: str) -> str:
    c = str(code6).zfill(6)
    if c.startswith(("5", "6", "9")):
        return f"{c}.SH"
    if c.startswith(("4", "8")):
        return f"{c}.BJ"
    return f"{c}.SZ"


def _load_daily(code6: str, cache: dict) -> pd.DataFrame | None:
    if code6 in cache:
        return cache[code6]
    fp = CACHE / f"{_full_code(code6)}.csv"
    if not fp.is_file():
        for suf in (".SZ", ".SH", ".BJ"):
            alt = CACHE / f"{code6}{suf}.csv"
            if alt.is_file():
                fp = alt
                break
        else:
            cache[code6] = None
            return None
    d = pd.read_csv(fp)
    d["date"] = pd.to_datetime(d["date"]).dt.normalize()
    d["close"] = pd.to_numeric(d["close"], errors="coerce")
    d = d.dropna(subset=["date", "close"]).sort_values("date").reset_index(drop=True)
    cache[code6] = d
    return d


def _ma_at(closes: np.ndarray, n: int) -> float:
    if len(closes) < n:
        return float("nan")
    return float(np.mean(closes[-n:]))


def _parse_pct(v) -> float:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return float("nan")
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("%", "").replace(",", "")
    try:
        return float(s)
    except ValueError:
        return float("nan")


def enrich(df: pd.DataFrame) -> pd.DataFrame:
    cache: dict = {}
    rows = []
    for _, r in df.iterrows():
        code = _norm_code(r.get("股票代码") or r.get("代码"))
        sel = pd.Timestamp(r["选股日"]).normalize()
        daily = _load_daily(code, cache)
        ma = {k: float("nan") for k in (5, 10, 20, 30, 60, 120)}
        if daily is not None and not daily.empty:
            # 买入日早盘口径：用到选股日收盘（含选股日）
            prior = daily[daily["date"] <= sel]["close"].to_numpy(dtype=float)
            for n in ma:
                ma[n] = _ma_at(prior, n)
        out = r.to_dict()
        out["代码"] = code
        out["股票代码"] = code
        out["5日线"] = round(ma[5], 4) if ma[5] == ma[5] else np.nan
        out["10日线"] = round(ma[10], 4) if ma[10] == ma[10] else np.nan
        out["20日线"] = round(ma[20], 4) if ma[20] == ma[20] else np.nan
        out["30日线"] = round(ma[30], 4) if ma[30] == ma[30] else np.nan
        out["60日线"] = round(ma[60], 4) if ma[60] == ma[60] else np.nan
        out["120日线"] = round(ma[120], 4) if ma[120] == ma[120] else np.nan
        out["MA5_gt_MA10"] = bool(ma[5] == ma[5] and ma[10] == ma[10] and ma[5] > ma[10])
        longer_full = [ma[k] for k in (20, 30, 60, 120)]
        longer_weak = [ma[k] for k in (20, 30, 60)]
        out["MA齐全"] = all(x == x for x in [ma[5], ma[10]])
        # 完整严多头需 MA120；缓存常不足 → 另给「弱严多头」= MA5>MA10 且 >max(20/30/60)
        if all(x == x for x in [ma[5], ma[10], *longer_full]):
            out["严多头"] = bool(ma[5] > ma[10] and ma[5] > max(longer_full))
        else:
            out["严多头"] = False
        if all(x == x for x in [ma[5], ma[10], *longer_weak]):
            out["弱严多头"] = bool(ma[5] > ma[10] and ma[5] > max(longer_weak))
            out["MA60齐全"] = True
        else:
            out["弱严多头"] = False
            out["MA60齐全"] = False
        out["序号"] = pd.to_numeric(r.get("序号"), errors="coerce")
        out["净占比"] = _parse_pct(r.get("今日主力净流入-净占比"))
        out["开盘涨跌幅pct"] = pd.to_numeric(r.get("开盘涨跌幅pct"), errors="coerce")
        rows.append(out)
    return pd.DataFrame(rows)


def _stats(sub: pd.DataFrame, label: str) -> dict:
    if sub.empty:
        return {"label": label, "n": 0, "mean": None, "med": None, "wr": None}
    ret = sub["收益率pct"].astype(float)
    return {
        "label": label,
        "n": int(len(sub)),
        "mean": round(float(ret.mean()), 3),
        "med": round(float(ret.median()), 3),
        "wr": round(float((ret > 0).mean() * 100), 1),
    }


def analyze(df: pd.DataFrame) -> dict:
    base = _stats(df, "全样本(日线弱化)")
    has_ma = df.loc[df["MA齐全"]].copy()
    has60 = df.loc[df["MA60齐全"]].copy()
    gap = df["开盘涨跌幅pct"]
    rank = df["序号"]
    nz = df["净占比"]

    def _mask(frame: pd.DataFrame, m: pd.Series) -> pd.DataFrame:
        return frame.loc[m.reindex(frame.index).fillna(False)]

    slices = [
        ("全样本", df),
        ("MA齐全子集", has_ma),
        ("MA5>MA10", _mask(has_ma, has_ma["MA5_gt_MA10"])),
        ("MA5≤MA10", _mask(has_ma, ~has_ma["MA5_gt_MA10"])),
        ("弱严多头(无MA120)", _mask(has60, has60["弱严多头"])),
        ("非弱严多头(MA60齐全)", _mask(has60, ~has60["弱严多头"])),
        ("严多头(完整)", _mask(has_ma, has_ma["严多头"])),
        ("名次1-10", df.loc[rank.between(1, 10)]),
        ("名次1-20", df.loc[rank.between(1, 20)]),
        ("名次11-50", df.loc[rank.between(11, 50)]),
        ("名次51-100", df.loc[rank.between(51, 100)]),
        ("高开≥3%", df.loc[gap >= 3]),
        ("高开1-3%", df.loc[gap.between(1, 3)]),
        ("平开-1~1%", df.loc[gap.between(-1, 1)]),
        ("低开≤-1%", df.loc[gap <= -1]),
        ("净占比≥5%", df.loc[nz >= 5]),
        ("净占比2-5%", df.loc[nz.between(2, 5)]),
        ("净占比0-2%", df.loc[nz.between(0, 2)]),
        # 叠加
        ("MA5>MA10∩名次1-20", _mask(has_ma, has_ma["MA5_gt_MA10"] & rank.reindex(has_ma.index).between(1, 20))),
        ("MA5>MA10∩高开1-3%", _mask(has_ma, has_ma["MA5_gt_MA10"] & gap.reindex(has_ma.index).between(1, 3))),
        ("MA5>MA10∩高开≥3%", _mask(has_ma, has_ma["MA5_gt_MA10"] & (gap.reindex(has_ma.index) >= 3))),
        ("MA5>MA10∩低开≤-1%", _mask(has_ma, has_ma["MA5_gt_MA10"] & (gap.reindex(has_ma.index) <= -1))),
        ("弱严多头∩名次1-20", _mask(has60, has60["弱严多头"] & rank.reindex(has60.index).between(1, 20))),
        ("弱严多头∩高开1-3%", _mask(has60, has60["弱严多头"] & gap.reindex(has60.index).between(1, 3))),
        ("弱严多头∩低开≤-1%", _mask(has60, has60["弱严多头"] & (gap.reindex(has60.index) <= -1))),
        ("弱严多头∩名次1-20∩低开", _mask(
            has60,
            has60["弱严多头"]
            & rank.reindex(has60.index).between(1, 20)
            & (gap.reindex(has60.index) <= -1),
        )),
        ("名次1-10∩低开≤-1%", df.loc[rank.between(1, 10) & (gap <= -1)]),
        ("名次1-20∩低开≤-1%", df.loc[rank.between(1, 20) & (gap <= -1)]),
        ("MA5≤MA10∩低开≤-1%", _mask(has_ma, (~has_ma["MA5_gt_MA10"]) & (gap.reindex(has_ma.index) <= -1))),
    ]

    combos = [_stats(sub, lab) for lab, sub in slices]
    combos_sorted = sorted(
        [c for c in combos if c["n"] >= 30 and c["mean"] is not None],
        key=lambda x: x["mean"],
        reverse=True,
    )

    by_month = (
        df.assign(ym=pd.to_datetime(df["选股日"]).dt.to_period("M").astype(str))
        .groupby("ym")["收益率pct"]
        .agg(n="count", mean="mean", wr=lambda s: (s > 0).mean() * 100)
        .round(3)
        .reset_index()
        .to_dict(orient="records")
    )

    # 关键过滤按月稳健性
    key_filters = [
        "MA5>MA10",
        "MA5≤MA10",
        "弱严多头(无MA120)",
        "低开≤-1%",
        "名次1-10",
        "MA5>MA10∩名次1-20",
        "名次1-20∩低开≤-1%",
        "高开≥3%",
    ]
    month_x = {}
    smap = dict(slices)
    for lab in key_filters:
        sub = smap[lab]
        if sub.empty:
            month_x[lab] = []
            continue
        g = (
            sub.assign(ym=pd.to_datetime(sub["选股日"]).dt.to_period("M").astype(str))
            .groupby("ym")["收益率pct"]
            .agg(n="count", mean="mean", wr=lambda s: (s > 0).mean() * 100)
            .round(3)
        )
        month_x[lab] = g.reset_index().to_dict(orient="records")

    return {
        "base": base,
        "ma_coverage": {
            "n_all": int(len(df)),
            "n_ma5_10": int(df["MA齐全"].sum()),
            "n_ma60": int(df["MA60齐全"].sum()),
            "n_strict_possible": int(
                df[["5日线", "10日线", "20日线", "30日线", "60日线", "120日线"]]
                .notna()
                .all(axis=1)
                .sum()
            ),
        },
        "combos": combos,
        "combos_sorted": combos_sorted,
        "by_month": by_month,
        "month_x_filter": month_x,
        "slices_for_export": {
            lab: sub
            for lab, sub in slices
            if lab
            in (
                "全样本",
                "MA5>MA10",
                "MA5≤MA10",
                "弱严多头(无MA120)",
                "低开≤-1%",
                "名次1-10",
                "MA5>MA10∩名次1-20",
                "名次1-20∩低开≤-1%",
            )
        },
    }


def main():
    print("读取", SRC)
    df = pd.read_excel(SRC, dtype={"代码": str, "股票代码": str})
    print(f"原始 {len(df)} 笔，回填均线…")
    en = enrich(df)
    print(
        f"MA5/10齐全 {int(en['MA齐全'].sum())}/{len(en)}；"
        f"MA60齐全 {int(en['MA60齐全'].sum())}；"
        f"完整严多头可判 "
        f"{int(en[['5日线','10日线','20日线','30日线','60日线','120日线']].notna().all(axis=1).sum())}"
    )
    res = analyze(en)

    OUT_XLSX.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        en.to_excel(w, sheet_name="明细_含均线", index=False)
        pd.DataFrame(res["combos"]).to_excel(w, sheet_name="过滤对照", index=False)
        pd.DataFrame(res["combos_sorted"]).to_excel(w, sheet_name="对照_按笔均排序", index=False)
        for lab, sub in res["slices_for_export"].items():
            name = lab.replace("∩", "_").replace("≥", "ge").replace(">", "gt")[:31]
            sub.to_excel(w, sheet_name=name, index=False)

    OUT_JSON.write_text(json.dumps({
        k: v for k, v in res.items() if k != "slices_for_export"
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print("写出", OUT_XLSX)
    print("基线", res["base"])
    print("覆盖", res["ma_coverage"])
    print("\n按笔均 Top10 (n≥30):")
    for c in res["combos_sorted"][:10]:
        print(f"  {c['label']:28s} n={c['n']:5d}  mean={c['mean']:+.3f}%  wr={c['wr']:.1f}%")
    print("\n按笔均 Bottom5 (n≥30):")
    for c in res["combos_sorted"][-5:]:
        print(f"  {c['label']:28s} n={c['n']:5d}  mean={c['mean']:+.3f}%  wr={c['wr']:.1f}%")


if __name__ == "__main__":
    main()
