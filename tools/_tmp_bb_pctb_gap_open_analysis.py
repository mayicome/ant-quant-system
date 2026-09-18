# -*- coding: utf-8 -*-
"""布林%b：买入日高开/低开 vs 收益。

日线用 load_daily_bars(adjust=qfq) → daily_full_qfq + daily_cache_qfq 合并。
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DIR = ROOT / "history_data" / "布林%b回落选股"
OUT = DIR / "_tmp_bb_pctb_gap_open_analysis.json"


def _stats(a: pd.Series) -> Dict[str, Any]:
    a = pd.to_numeric(a, errors="coerce").dropna()
    if a.empty:
        return {"n": 0}
    return {
        "n": int(len(a)),
        "mean": round(float(a.mean()), 3),
        "median": round(float(a.median()), 3),
        "win": round(float((a > 0).mean() * 100), 1),
    }


class Bars:
    def __init__(self) -> None:
        self._df: Dict[str, Optional[pd.DataFrame]] = {}

    def load(self, code: str) -> Optional[pd.DataFrame]:
        if code in self._df:
            return self._df[code]
        from utils.daily_cache_reader import load_daily_bars

        try:
            df = load_daily_bars(code, through_date=None, adjust="qfq")
        except Exception:
            df = None
        if df is None or getattr(df, "empty", True):
            try:
                df = load_daily_bars(code, through_date=None, adjust="none")
            except Exception:
                df = None
        if df is None or getattr(df, "empty", True):
            self._df[code] = None
            return None
        df = df.copy()
        dcol = "date" if "date" in df.columns else "日期"
        df["_d"] = pd.to_datetime(df[dcol]).dt.date
        df = df.sort_values("_d").drop_duplicates("_d", keep="last")
        self._df[code] = df
        return df

    def ohlc(self, code: str, d: date) -> Optional[Dict[str, float]]:
        df = self.load(code)
        if df is None:
            return None
        sub = df[df["_d"] == d]
        if sub.empty:
            return None
        row = sub.iloc[-1]

        def f(*keys):
            for k in keys:
                if k in row.index:
                    try:
                        v = float(row.get(k) or 0)
                    except (TypeError, ValueError):
                        v = 0.0
                    if v == v and v > 0:
                        return v
            return 0.0

        o, c = f("open", "开盘"), f("close", "收盘")
        if o <= 0 and c <= 0:
            return None
        if o <= 0:
            o = c
        if c <= 0:
            c = o
        return {"open": o, "close": c}

    def prev_close(self, code: str, d: date) -> Optional[float]:
        df = self.load(code)
        if df is None:
            return None
        sub = df[df["_d"] < d]
        if sub.empty:
            return None
        row = sub.iloc[-1]
        for k in ("close", "收盘"):
            if k in row.index:
                try:
                    v = float(row.get(k) or 0)
                except (TypeError, ValueError):
                    v = 0.0
                if v > 0:
                    return v
        return None


def main() -> int:
    buy = pd.read_csv(DIR / "回测成交明细_日线-bb_pctb-bb_pctb_sell买入_latest.csv", encoding="utf-8-sig")
    ret = pd.read_excel(DIR / "各日选股收益汇总_日线-bb_pctb-bb_pctb_sell-单点_按票_已完成_latest.xlsx")

    buy["code"] = buy["代码"].astype(str).str.zfill(6)
    buy["sel"] = pd.to_datetime(buy["选股日"]).dt.strftime("%Y-%m-%d")
    buy["buy_d"] = pd.to_datetime(buy["日期"]).dt.date
    buy = buy.sort_values(["code", "sel", "日期"]).groupby(["code", "sel"], as_index=False).first()

    ret = ret.copy()
    ret["code"] = ret["代码"].astype(str).str.zfill(6)
    ret["sel"] = pd.to_datetime(ret["选股日"]).dt.strftime("%Y-%m-%d")
    ret["ret"] = pd.to_numeric(ret["收益率pct"], errors="coerce")

    m = buy.merge(ret[["code", "sel", "ret"]], on=["code", "sel"], how="inner")
    bars = Bars()

    rows = []
    miss = 0
    for i, r in m.iterrows():
        code = str(r["code"])
        bd = r["buy_d"]
        pre = bars.prev_close(code, bd)
        bar = bars.ohlc(code, bd)
        if pre is None or pre <= 0 or bar is None:
            miss += 1
            continue
        open_px = float(bar["open"])
        gap = (open_px / pre - 1.0) * 100.0
        sel_d = date.fromisoformat(str(r["sel"]))
        sel_bar = bars.ohlc(code, sel_d)
        gap_vs_sel = None
        if sel_bar and sel_bar["close"] > 0:
            gap_vs_sel = (open_px / float(sel_bar["close"]) - 1.0) * 100.0
        rows.append(
            {
                "code": code,
                "sel": r["sel"],
                "gap_pct": round(gap, 4),
                "gap_vs_sel_pct": None if gap_vs_sel is None else round(gap_vs_sel, 4),
                "ret": float(r["ret"]),
            }
        )
        if len(rows) % 400 == 0:
            print(f"… {len(rows)} ok / miss {miss}", flush=True)

    df = pd.DataFrame(rows).dropna(subset=["ret", "gap_pct"])
    print(f"n={len(df)} miss={miss} mean_ret={df['ret'].mean():.3f}", flush=True)

    bins = [-np.inf, -3, -1.5, -0.5, 0.5, 1.5, 3, np.inf]
    labels = ["≤-3%", "-3~-1.5%", "-1.5~-0.5%", "-0.5~+0.5%", "+0.5~+1.5%", "+1.5~+3%", ">+3%"]
    df["gap_bin"] = pd.cut(df["gap_pct"], bins=bins, labels=labels)

    by_bin = []
    for lab in labels:
        sub = df[df["gap_bin"] == lab]["ret"]
        st = _stats(sub)
        st["bin"] = lab
        st["share"] = round(float(len(sub) / max(len(df), 1) * 100), 1)
        by_bin.append(st)

    coarse_labs = ["低开(<-0.3%)", "平开(±0.3%)", "高开(>0.3%)"]
    df["coarse"] = pd.cut(df["gap_pct"], bins=[-np.inf, -0.3, 0.3, np.inf], labels=coarse_labs)
    by_coarse = []
    for lab in coarse_labs:
        sub = df[df["coarse"] == lab]["ret"]
        st = _stats(sub)
        st["bin"] = lab
        st["share"] = round(float(len(sub) / max(len(df), 1) * 100), 1)
        by_coarse.append(st)

    df2 = df.dropna(subset=["gap_vs_sel_pct"]).copy()
    df2["vs_sel_bin"] = pd.cut(df2["gap_vs_sel_pct"], bins=bins, labels=labels)
    by_vs_sel = []
    for lab in labels:
        sub = df2[df2["vs_sel_bin"] == lab]["ret"]
        st = _stats(sub)
        st["bin"] = lab
        st["share"] = round(float(len(sub) / max(len(df2), 1) * 100), 1)
        by_vs_sel.append(st)

    filters = []
    for name, mask in [
        ("全部（有行情）", pd.Series(True, index=df.index)),
        ("仅低开 gap<-0.3%", df["gap_pct"] < -0.3),
        ("低开或平开 gap≤0.3%", df["gap_pct"] <= 0.3),
        ("排除高开>1%", df["gap_pct"] <= 1.0),
        ("排除高开>1.5%", df["gap_pct"] <= 1.5),
        ("排除高开>2%", df["gap_pct"] <= 2.0),
        ("仅高开>0.3%", df["gap_pct"] > 0.3),
        ("仅高开>1.5%", df["gap_pct"] > 1.5),
        ("温和低开 -3%~-0.5%", (df["gap_pct"] >= -3) & (df["gap_pct"] < -0.5)),
    ]:
        st = _stats(df.loc[mask, "ret"])
        st["rule"] = name
        st["kept_pct"] = round(float(mask.mean() * 100), 1)
        filters.append(st)

    filters_sel = []
    for name, mask in [
        ("全部", pd.Series(True, index=df2.index)),
        ("相对选股收盘≤0%", df2["gap_vs_sel_pct"] <= 0),
        ("相对选股收盘≤1%", df2["gap_vs_sel_pct"] <= 1),
        ("跳过相对选股收>2%", df2["gap_vs_sel_pct"] <= 2),
        ("仅相对选股收>2%", df2["gap_vs_sel_pct"] > 2),
    ]:
        st = _stats(df2.loc[mask, "ret"])
        st["rule"] = name
        st["kept_pct"] = round(float(mask.mean() * 100), 1)
        filters_sel.append(st)

    out = {
        "meta": {
            "n": int(len(df)),
            "miss": int(miss),
            "n_merge": int(len(m)),
            "ohlc_src": "load_daily_bars(adjust=qfq) → daily_full_qfq + daily_cache_qfq",
            "gap_def": "买入日开盘/昨收 - 1（%）；另：开盘/选股日收盘 - 1",
            "corr_gap_ret": round(float(df["gap_pct"].corr(df["ret"])), 4),
            "corr_gap_vs_sel_ret": round(float(df2["gap_vs_sel_pct"].corr(df2["ret"])), 4)
            if len(df2) > 2
            else None,
            "gap_describe": {
                "mean": round(float(df["gap_pct"].mean()), 3),
                "median": round(float(df["gap_pct"].median()), 3),
                "p10": round(float(df["gap_pct"].quantile(0.1)), 3),
                "p90": round(float(df["gap_pct"].quantile(0.9)), 3),
            },
            "ret_covered_mean": round(float(df["ret"].mean()), 3),
        },
        "by_gap_vs_preclose": by_bin,
        "by_coarse": by_coarse,
        "by_gap_vs_sel_close": by_vs_sel,
        "filter_preclose": filters,
        "filter_vs_sel": filters_sel,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT)
    print(json.dumps(out["meta"], ensure_ascii=False, indent=2))
    print("coarse", json.dumps(by_coarse, ensure_ascii=False))
    print("filters", json.dumps(filters, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
