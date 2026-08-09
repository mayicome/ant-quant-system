# -*- coding: utf-8 -*-
"""七月跳过日规则扫描：仅用买入前可知特征，躲开普跌选股日。

结果日收益：history_data/八月回测-热门/各日选股收益汇总.xlsx（无 Cond123）
目标坏日：7/3、7/6、7/9（及「普跌日」mean<-2% 且 win<25%）

买入前可用：
  - 选股日收盘后：大盘/广度（全日线等权）、东财概念涨幅、选股池结构
  - 买入日 09:25 前：池子相对昨收的开盘缺口（用日线 open）
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(r"d:\蚂蚁量化系统\history_data\八月回测-热门")
DAILY = Path(r"d:\蚂蚁量化系统\data\daily_cache")
EM = Path(r"d:\蚂蚁量化系统\data\eastmoney_board_rank")
OUT_XLSX = ROOT / "跳过日规则扫描_七月.xlsx"
OUT_JSON = ROOT / "_skip_day_screen_july.json"

TARGET_BAD = {"2026-07-03", "2026-07-06", "2026-07-09"}
FULL_SUM = ROOT / "各日选股收益汇总.xlsx"
SEL_FULL = ROOT / "选股结果_东财热门-besttest全量-无个股过滤_2026-07-01_2026-07-31.xls"
SEL_FILT = ROOT / (
    "选股结果_东财热门-besttest-无涨停均线差0.5to2-MA空头排列_2026-07-01_2026-07-31.xls"
)


def _day_s(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s).dt.strftime("%Y-%m-%d")


def _code6(s: pd.Series) -> pd.Series:
    x = s.astype(str).str.extract(r"(\d+)", expand=False)
    return x.str.zfill(6)


def _symbol_path(code6: str) -> Path | None:
    for suf in (".SZ", ".SH", ".BJ"):
        p = DAILY / f"{code6}{suf}.csv"
        if p.is_file():
            return p
    # try bare
    hits = list(DAILY.glob(f"{code6}.*.csv"))
    return hits[0] if hits else None


def load_day_outcomes() -> pd.DataFrame:
    df = pd.read_excel(FULL_SUM)
    df["_d"] = _day_s(df["选股日"])
    df["收益率pct"] = pd.to_numeric(df["收益率pct"], errors="coerce")
    g = (
        df.groupby("_d", as_index=False)["收益率pct"]
        .agg(n="count", mean="mean", median="median", win=lambda s: (s > 0).mean() * 100)
        .rename(columns={"_d": "选股日"})
    )
    g["is_target_bad"] = g["选股日"].isin(TARGET_BAD)
    g["is_ugly"] = (g["mean"] < -2.0) & (g["win"] < 25.0)
    # buy day = next calendar row in this july set
    days = sorted(g["选股日"])
    nxt = {days[i]: days[i + 1] for i in range(len(days) - 1)}
    prev = {days[i]: days[i - 1] for i in range(1, len(days))}
    g["买入日"] = g["选股日"].map(nxt)
    g["前一日"] = g["选股日"].map(prev)
    return g


def build_market_panel(dates: list[str]) -> pd.DataFrame:
    """等权：全日线文件在 dates 上的涨跌幅/上涨占比。"""
    need = set(dates)
    # also need prev for first day — expand with neighbors from files later
    rows_by_date: dict[str, list[float]] = {d: [] for d in need}
    files = list(DAILY.glob("*.csv"))
    print("scanning daily_cache files:", len(files))
    for i, p in enumerate(files):
        if i and i % 1000 == 0:
            print("  ...", i)
        try:
            df = pd.read_csv(p, usecols=["date", "close"])
        except Exception:
            continue
        df["date"] = _day_s(df["date"])
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna().sort_values("date")
        df["ret"] = df["close"].pct_change() * 100.0
        sub = df[df["date"].isin(need)]
        for _, r in sub.iterrows():
            if r["ret"] == r["ret"]:
                rows_by_date[r["date"]].append(float(r["ret"]))
    out = []
    for d, rets in sorted(rows_by_date.items()):
        arr = np.array(rets, dtype=float)
        if len(arr) == 0:
            out.append(
                {
                    "date": d,
                    "mkt_n": 0,
                    "mkt_mean": np.nan,
                    "mkt_med": np.nan,
                    "mkt_up_pct": np.nan,
                }
            )
        else:
            out.append(
                {
                    "date": d,
                    "mkt_n": int(len(arr)),
                    "mkt_mean": float(arr.mean()),
                    "mkt_med": float(np.median(arr)),
                    "mkt_up_pct": float((arr > 0).mean() * 100),
                }
            )
    return pd.DataFrame(out)


def build_em_features(dates: list[str]) -> pd.DataFrame:
    rows = []
    for d in dates:
        p = EM / f"concept_rank_{d}.csv"
        if not p.is_file():
            rows.append({"date": d, "em_top20_mean": np.nan, "em_up_pct": np.nan})
            continue
        df = pd.read_csv(p, encoding="utf-8-sig")
        # find pct col
        pct_col = None
        for c in ("涨跌幅", "涨跌幅%", "f3"):
            if c in df.columns:
                pct_col = c
                break
        if pct_col is None:
            for c in df.columns:
                if "涨跌幅" in str(c):
                    pct_col = c
                    break
        if pct_col is None:
            rows.append({"date": d, "em_top20_mean": np.nan, "em_up_pct": np.nan})
            continue
        v = pd.to_numeric(df[pct_col], errors="coerce").dropna()
        if len(v) and v.abs().median() < 1:
            v = v * 100
        top = v.head(20)
        rows.append(
            {
                "date": d,
                "em_top20_mean": float(top.mean()) if len(top) else np.nan,
                "em_up_pct": float((v > 0).mean() * 100) if len(v) else np.nan,
            }
        )
    return pd.DataFrame(rows)


def build_pool_features(sel_path: Path, label: str) -> pd.DataFrame:
    df = pd.read_excel(sel_path)
    df["_d"] = _day_s(df["选股日"])
    code_col = "股票代码" if "股票代码" in df.columns else "代码"
    df["_c"] = _code6(df[code_col])
    rows = []
    for d, g in df.groupby("_d"):
        row = {
            "选股日": d,
            f"{label}_pool_n": int(len(g)),
        }
        for col, key in [
            ("合格榜内序位", "elig_mean"),
            ("合格榜标签内RS排名", "rs_mean"),
            ("均线差占比", "gap_mean"),
            ("近10日RS", "rs10_mean"),
        ]:
            if col in g.columns:
                v = pd.to_numeric(g[col], errors="coerce")
                row[f"{label}_{key}"] = float(v.mean()) if v.notna().any() else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


def build_buy_open_gap(sel_path: Path, day_map: pd.DataFrame) -> pd.DataFrame:
    """选股池在买入日的开盘相对昨收缺口（日线 open/prev_close-1）。"""
    sel = pd.read_excel(sel_path)
    sel["_d"] = _day_s(sel["选股日"])
    code_col = "股票代码" if "股票代码" in sel.columns else "代码"
    sel["_c"] = _code6(sel[code_col])
    buy_map = day_map.set_index("选股日")["买入日"].to_dict()

    # cache daily frames for needed codes
    codes = sorted(sel["_c"].unique())
    cache: dict[str, pd.DataFrame] = {}
    print("loading daily for open-gap codes:", len(codes))
    for i, c in enumerate(codes):
        if i and i % 500 == 0:
            print("  ...", i)
        p = _symbol_path(c)
        if p is None:
            continue
        try:
            df = pd.read_csv(p, usecols=["date", "open", "close"])
        except Exception:
            continue
        df["date"] = _day_s(df["date"])
        df["open"] = pd.to_numeric(df["open"], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna().sort_values("date")
        df["prev_close"] = df["close"].shift(1)
        cache[c] = df.set_index("date")

    rows = []
    for d, g in sel.groupby("_d"):
        buy_d = buy_map.get(d)
        if not buy_d:
            rows.append(
                {
                    "选股日": d,
                    "buy_open_gap_mean": np.nan,
                    "buy_open_up_pct": np.nan,
                    "buy_open_gap_med": np.nan,
                }
            )
            continue
        gaps = []
        for c in g["_c"]:
            bar = cache.get(c)
            if bar is None or buy_d not in bar.index:
                continue
            o = bar.at[buy_d, "open"]
            pc = bar.at[buy_d, "prev_close"]
            if pc and pc == pc and pc > 0 and o == o:
                gaps.append((o / pc - 1.0) * 100.0)
        arr = np.array(gaps, dtype=float)
        rows.append(
            {
                "选股日": d,
                "buy_open_gap_mean": float(arr.mean()) if len(arr) else np.nan,
                "buy_open_gap_med": float(np.median(arr)) if len(arr) else np.nan,
                "buy_open_up_pct": float((arr > 0).mean() * 100) if len(arr) else np.nan,
                "buy_open_n": int(len(arr)),
            }
        )
    return pd.DataFrame(rows)


def eval_rule(feat: pd.DataFrame, mask: pd.Series, name: str) -> dict:
    skipped = feat.loc[mask]
    kept = feat.loc[~mask]
    tgt = feat["is_target_bad"]
    ugly = feat["is_ugly"]
    # good day: mean >= 0
    good = feat["mean"] >= 0

    catch_tgt = int((mask & tgt).sum())
    miss_tgt = int((~mask & tgt).sum())
    false_skip_good = int((mask & good).sum())
    catch_ugly = int((mask & ugly).sum())
    n_ugly = int(ugly.sum())
    n_good = int(good.sum())

    return {
        "name": name,
        "skip_n": int(mask.sum()),
        "keep_n": int((~mask).sum()),
        "catch_target_3": catch_tgt,
        "miss_target": miss_tgt,
        "catch_ugly": catch_ugly,
        "ugly_total": n_ugly,
        "false_skip_good": false_skip_good,
        "good_total": n_good,
        "skipped_days": ",".join(sorted(skipped["选股日"].tolist())),
        "kept_mean": float(kept["mean"].mean()) if len(kept) else None,
        "base_mean": float(feat["mean"].mean()),
        "skipped_mean": float(skipped["mean"].mean()) if len(skipped) else None,
        "delta_keep_vs_base": (
            float(kept["mean"].mean() - feat["mean"].mean()) if len(kept) else None
        ),
    }


def main() -> None:
    outc = load_day_outcomes()
    dates = sorted(outc["选股日"].tolist())
    # include prev of first for mkt join
    extra = [d for d in outc["前一日"].dropna().unique().tolist() if d not in dates]
    mkt_dates = sorted(set(dates) | set(extra) | set(outc["买入日"].dropna()))

    print("building market panel...")
    mkt = build_market_panel(mkt_dates)
    print("building em features...")
    em = build_em_features(dates + extra)
    print("building pool features...")
    pool_f = build_pool_features(SEL_FULL, "full")
    pool_b = build_pool_features(SEL_FILT, "filt")
    print("building buy open gaps...")
    gaps = build_buy_open_gap(SEL_FULL, outc)

    feat = outc.copy()
    feat = feat.merge(mkt.rename(columns={"date": "选股日"}), on="选股日", how="left")
    feat = feat.merge(
        mkt.rename(
            columns={
                "date": "前一日",
                "mkt_mean": "mkt_mean_prev",
                "mkt_med": "mkt_med_prev",
                "mkt_up_pct": "mkt_up_pct_prev",
                "mkt_n": "mkt_n_prev",
            }
        )[
            [
                "前一日",
                "mkt_mean_prev",
                "mkt_med_prev",
                "mkt_up_pct_prev",
            ]
        ],
        on="前一日",
        how="left",
    )
    feat = feat.merge(em.rename(columns={"date": "选股日"}), on="选股日", how="left")
    feat = feat.merge(
        em.rename(
            columns={
                "date": "前一日",
                "em_top20_mean": "em_top20_mean_prev",
                "em_up_pct": "em_up_pct_prev",
            }
        )[["前一日", "em_top20_mean_prev", "em_up_pct_prev"]],
        on="前一日",
        how="left",
    )
    feat = feat.merge(pool_f, on="选股日", how="left")
    feat = feat.merge(pool_b, on="选股日", how="left")
    feat = feat.merge(gaps, on="选股日", how="left")

    # rename mkt on selection day
    feat = feat.rename(
        columns={
            "mkt_mean": "mkt_mean_sel",
            "mkt_med": "mkt_med_sel",
            "mkt_up_pct": "mkt_up_pct_sel",
        }
    )

    print("\n=== feature snapshot (target bad) ===")
    cols_show = [
        "选股日",
        "mean",
        "win",
        "mkt_mean_sel",
        "mkt_up_pct_sel",
        "mkt_mean_prev",
        "em_top20_mean",
        "buy_open_gap_mean",
        "buy_open_up_pct",
        "full_pool_n",
    ]
    print(feat.loc[feat["is_target_bad"], cols_show].to_string(index=False))
    print("\n=== all days ===")
    print(feat[cols_show + ["is_ugly"]].sort_values("选股日").to_string(index=False))

    rules = []

    def add(name: str, mask: pd.Series):
        rules.append(eval_rule(feat, mask.fillna(False), name))

    # single thresholds — selection day market
    for thr in [-1.0, -0.5, 0.0]:
        add("选股日等权涨幅<%.1f%%" % thr, feat["mkt_mean_sel"] < thr)
    for thr in [40, 45, 50]:
        add("选股日上涨家数占比<%d%%" % thr, feat["mkt_up_pct_sel"] < thr)
    for thr in [-1.0, -0.5, 0.0]:
        add("前一日等权涨幅<%.1f%%" % thr, feat["mkt_mean_prev"] < thr)
    for thr in [0.0, 1.0, 2.0]:
        add("东财概念Top20均涨<%.1f%%" % thr, feat["em_top20_mean"] < thr)
    # buy open gap
    for thr in [0.5, 0.0, -0.3, -0.5]:
        add("买入日池开盘缺口均<%.1f%%" % thr, feat["buy_open_gap_mean"] < thr)
    for thr in [45, 50, 55]:
        add("买入日池高开占比<%d%%" % thr, feat["buy_open_up_pct"] < thr)

    # combos focused on catching target
    add(
        "选股日上涨%<45 且 等权<0",
        (feat["mkt_up_pct_sel"] < 45) & (feat["mkt_mean_sel"] < 0),
    )
    add(
        "选股日上涨%<50 且 等权<-0.5",
        (feat["mkt_up_pct_sel"] < 50) & (feat["mkt_mean_sel"] < -0.5),
    )
    add(
        "选股日等权<0 或 买入日缺口均<0",
        (feat["mkt_mean_sel"] < 0) | (feat["buy_open_gap_mean"] < 0),
    )
    add(
        "选股日等权<-0.5 或 (上涨%<45)",
        (feat["mkt_mean_sel"] < -0.5) | (feat["mkt_up_pct_sel"] < 45),
    )
    add(
        "选股日上涨%<40",
        feat["mkt_up_pct_sel"] < 40,
    )
    add(
        "买入日缺口均<0 且 选股日等权<0.5",
        (feat["buy_open_gap_mean"] < 0) & (feat["mkt_mean_sel"] < 0.5),
    )
    add(
        "选股日等权<0 且 东财Top20<2",
        (feat["mkt_mean_sel"] < 0) & (feat["em_top20_mean"] < 2),
    )
    # 强选股日 → 次日弱开（针对 7/3 这类「选股日强、持有日弱」）
    add(
        "选股日上涨%>60 且 买入日高开%<50",
        (feat["mkt_up_pct_sel"] > 60) & (feat["buy_open_up_pct"] < 50),
    )
    add(
        "选股日等权>0.5 且 买入日缺口均<0.2",
        (feat["mkt_mean_sel"] > 0.5) & (feat["buy_open_gap_mean"] < 0.2),
    )
    add(
        "选股日上涨%>55 且 买入日缺口均<=0.1",
        (feat["mkt_up_pct_sel"] > 55) & (feat["buy_open_gap_mean"] <= 0.1),
    )
    # 弱选股日（吃 7/6）
    add(
        "选股日上涨%<35",
        feat["mkt_up_pct_sel"] < 35,
    )
    add(
        "选股日等权<-1",
        feat["mkt_mean_sel"] < -1.0,
    )
    # 组合：弱日 或 强转弱开
    add(
        "弱选股日(涨%<35) 或 强转弱开(涨%>60且高开%<50)",
        (feat["mkt_up_pct_sel"] < 35)
        | ((feat["mkt_up_pct_sel"] > 60) & (feat["buy_open_up_pct"] < 50)),
    )
    add(
        "弱选股日(等权<-1) 或 强转弱开(等权>0.5且缺口<0.2)",
        (feat["mkt_mean_sel"] < -1.0)
        | ((feat["mkt_mean_sel"] > 0.5) & (feat["buy_open_gap_mean"] < 0.2)),
    )
    add(
        "买入日高开%<40",
        feat["buy_open_up_pct"] < 40,
    )
    add(
        "买入日高开%<35",
        feat["buy_open_up_pct"] < 35,
    )

    rdf = pd.DataFrame(rules)
    # rank: catch all 3 target, then min false_skip_good, then max kept_mean
    rdf["perfect_target"] = rdf["catch_target_3"] >= 3
    rdf = rdf.sort_values(
        ["perfect_target", "catch_target_3", "false_skip_good", "kept_mean"],
        ascending=[False, False, True, False],
    )

    # correlation of features with day mean (descriptive)
    feat_cols = [
        c
        for c in feat.columns
        if c.startswith(("mkt_", "em_", "buy_", "full_", "filt_"))
        and feat[c].dtype != object
    ]
    corrs = []
    for c in feat_cols:
        s = feat[[c, "mean"]].dropna()
        if len(s) >= 8:
            corrs.append({"feature": c, "corr_with_day_mean": float(s[c].corr(s["mean"]))})
    corr_df = pd.DataFrame(corrs).sort_values("corr_with_day_mean")

    with pd.ExcelWriter(OUT_XLSX, engine="openpyxl") as w:
        feat.sort_values("选股日").to_excel(w, sheet_name="日特征与结果", index=False)
        rdf.to_excel(w, sheet_name="规则扫描", index=False)
        corr_df.to_excel(w, sheet_name="特征相关", index=False)
        # top candidates detail
        top = rdf.head(12)
        top.to_excel(w, sheet_name="优先候选", index=False)

    out = {
        "target_bad": sorted(TARGET_BAD),
        "ugly_days": sorted(feat.loc[feat["is_ugly"], "选股日"].tolist()),
        "ranked": rdf.head(15).replace({np.nan: None}).to_dict("records"),
        "corr": corr_df.replace({np.nan: None}).to_dict("records"),
        "feat_target": feat.loc[feat["is_target_bad"], cols_show]
        .replace({np.nan: None})
        .to_dict("records"),
    }
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("wrote", OUT_XLSX)
    print(rdf.head(15).to_string(index=False))


if __name__ == "__main__":
    main()
