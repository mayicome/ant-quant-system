# -*- coding: utf-8 -*-
"""
忽略现有封单评级，用原始特征 vs 次日收盘涨幅找更强规律。
"""
from __future__ import annotations

import re
import warnings
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore", category=FutureWarning)

ROOT = Path(r"d:\蚂蚁量化系统")
HIST = ROOT / "history_data"
DAILY = ROOT / "data" / "daily_cache"
OUT = HIST / f"封单特征探索_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

STAB_ORDER = {
    "封单极度稳定": 3,
    "封单整体平稳": 2,
    "封单剧烈波动": 1,
}
TREND_ORDER = {
    "趋势持续加强": 3,
    "封单整体平稳": 2,
    "趋势持续减弱": 1,
}
CONF_ORDER = {
    "高": 3,
    "高置信": 3,
    "中": 2,
    "中置信": 2,
    "低": 1,
    "低置信": 1,
}
RATING_ORDER = {
    "🔴 虚封高危": 1,
    "🟠 弱势封板": 2,
    "🟡 中等封板": 3,
    "🟢 强势封板": 4,
    "🔥 超强极致封板": 5,
}


def normalize_code(v: object) -> str:
    s = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", str(v or "").strip().upper())
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else ""


def find_col(df: pd.DataFrame, aliases: List[str]) -> Optional[str]:
    lower = {str(c).strip().lower(): c for c in df.columns}
    for a in aliases:
        if a.strip().lower() in lower:
            return lower[a.strip().lower()]
    for a in aliases:
        k = a.strip().lower()
        for c in df.columns:
            if k in str(c).strip().lower():
                return c
    return None


def parse_pct(v: object) -> float:
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return float("nan")
    if isinstance(v, (int, float)):
        x = float(v)
        # 若已是 0~1 小数且很小，也可能是比例；表格多为 "12.3%" 或 12.3
        return x * 100 if 0 < x <= 1.5 else x
    s = str(v).strip().replace("%", "").replace(",", "")
    if not s or s.lower() in ("nan", "none", "-"):
        return float("nan")
    try:
        return float(s)
    except ValueError:
        return float("nan")


def limit_ratio(code6: str) -> float:
    if code6.startswith(("300", "301", "688", "689")):
        return 0.20
    if code6.startswith(("8", "4", "920")):
        return 0.30
    return 0.10


def is_limit_up(prev_close: float, close: float, code6: str) -> bool:
    if prev_close <= 0 or close <= 0:
        return False
    r = limit_ratio(code6)
    limit_px = round(prev_close * (1 + r), 2)
    return abs(close - limit_px) <= 0.02 or (close / prev_close - 1.0) >= r * 0.99


_daily: Dict[str, Optional[pd.DataFrame]] = {}


def load_daily(code6: str) -> Optional[pd.DataFrame]:
    if code6 in _daily:
        return _daily[code6]
    p = None
    for suf in (".SZ", ".SH", ".BJ"):
        cand = DAILY / f"{code6}{suf}.csv"
        if cand.is_file():
            p = cand
            break
    if p is None:
        hits = list(DAILY.glob(f"{code6}.*.csv"))
        p = hits[0] if hits else None
    if p is None:
        _daily[code6] = None
        return None
    df = pd.read_csv(p)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["date"]).sort_values("date")
    for c in ("open", "high", "low", "close", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    _daily[code6] = df
    return df


def next_day_ret(code6: str, seal_ymd: str) -> Tuple[Optional[float], Optional[bool]]:
    df = load_daily(code6)
    if df is None or df.empty:
        return None, None
    seal_dt = pd.Timestamp(datetime.strptime(seal_ymd, "%Y%m%d"))
    before = df[df["date"] <= seal_dt]
    if before.empty:
        return None, None
    seal_row = before.iloc[-1]
    seal_close = float(seal_row["close"] or 0)
    if seal_close <= 0:
        return None, None
    after = df[df["date"] > seal_row["date"]]
    if after.empty:
        return None, None
    nxt_close = float(after.iloc[0]["close"] or 0)
    if nxt_close <= 0:
        return None, None
    ret = (nxt_close / seal_close - 1.0) * 100.0
    return ret, is_limit_up(seal_close, nxt_close, code6)


def list_seal_files() -> List[Tuple[str, Path]]:
    out = []
    for p in HIST.glob("封单结构_*.xlsx"):
        name = p.name
        if any(x in name for x in ("含次日", "滚动", "评估", "参数", "特征探索")):
            continue
        if name.startswith("~$"):
            continue
        m = re.search(r"(\d{8})", name)
        if m:
            out.append((m.group(1), p))
    return sorted(out, key=lambda x: x[0])


def map_cat(v: object, mapping: Dict[str, int]) -> Tuple[str, float]:
    s = str(v or "").strip()
    if not s or s.lower() in ("nan", "none"):
        return "", float("nan")
    if s in mapping:
        return s, float(mapping[s])
    for k, val in mapping.items():
        if k in s or s in k:
            return k, float(val)
    return s, float("nan")


def load_pool() -> pd.DataFrame:
    rows = []
    files = list_seal_files()
    print(f"读取 {len(files)} 份封单结构…")
    for i, (seal_ymd, path) in enumerate(files, 1):
        raw = pd.read_excel(path)
        code_c = find_col(raw, ["股票代码", "代码", "code"])
        name_c = find_col(raw, ["股票名称", "名称", "name"])
        amt_c = find_col(raw, ["收盘封单金额(亿)", "close_order_amount_yi", "封单金额"])
        hard_c = find_col(raw, ["封板硬度", "seal_hardness"])
        rush_c = find_col(raw, ["抢筹烈度", "rush_intensity"])
        stab_c = find_col(raw, ["封单稳定性", "order_stability"])
        trend_c = find_col(raw, ["封单运行趋势", "order_trend"])
        conf_c = find_col(raw, ["置信度", "confidence_tag"])
        rating_c = find_col(raw, ["封单评级", "order_rating"])
        score_c = find_col(raw, ["评级分值", "rating_score"])
        if code_c is None:
            continue
        for _, r in raw.iterrows():
            code = normalize_code(r[code_c])
            if not code:
                continue
            ret, lim = next_day_ret(code, seal_ymd)
            stab_s, stab_n = map_cat(r[stab_c] if stab_c else None, STAB_ORDER)
            trend_s, trend_n = map_cat(r[trend_c] if trend_c else None, TREND_ORDER)
            conf_s, conf_n = map_cat(r[conf_c] if conf_c else None, CONF_ORDER)
            rating_s = str(r[rating_c]).strip() if rating_c else ""
            score = pd.to_numeric(r[score_c], errors="coerce") if score_c else np.nan
            if pd.isna(score) and rating_s in RATING_ORDER:
                score = RATING_ORDER[rating_s]
            rows.append(
                {
                    "seal_date": seal_ymd,
                    "code": code,
                    "name": str(r[name_c]).strip() if name_c else "",
                    "amt": pd.to_numeric(r[amt_c], errors="coerce") if amt_c else np.nan,
                    "hardness": parse_pct(r[hard_c]) if hard_c else np.nan,
                    "rush": parse_pct(r[rush_c]) if rush_c else np.nan,
                    "stability": stab_s,
                    "stability_n": stab_n,
                    "trend": trend_s,
                    "trend_n": trend_n,
                    "confidence": conf_s,
                    "confidence_n": conf_n,
                    "rating": rating_s,
                    "rating_score": score,
                    "next_day_ret": ret,
                    "next_day_limit_up": lim,
                }
            )
        if i % 20 == 0 or i == len(files):
            print(f"  {i}/{len(files)} rows={len(rows)}")
    df = pd.DataFrame(rows)
    df = df.dropna(subset=["next_day_ret"]).copy()
    # 衍生
    df["log_amt"] = np.log1p(df["amt"].clip(lower=0))
    df["hard_x_rush"] = df["hardness"] * df["rush"]
    df["amt_x_hard"] = df["amt"] * df["hardness"]
    df["stable_strong"] = (df["stability_n"] >= 3) & (df["trend_n"] >= 3)
    df["stable_ok"] = (df["stability_n"] >= 2) & (df["trend_n"] >= 2)
    df["volatile"] = df["stability_n"] <= 1
    df["weakening"] = df["trend_n"] <= 1
    return df


def stats_group(g: pd.DataFrame) -> dict:
    rets = g["next_day_ret"]
    lims = g["next_day_limit_up"].dropna()
    return {
        "n": len(g),
        "均涨%": float(rets.mean()),
        "中位%": float(rets.median()),
        "胜率%": float((rets > 0).mean() * 100),
        "大亏率%": float((rets < -5).mean() * 100),
        "涨停率%": float(lims.mean() * 100) if len(lims) else np.nan,
    }


def quantile_table(df: pd.DataFrame, col: str, q: int = 5) -> pd.DataFrame:
    x = df.dropna(subset=[col, "next_day_ret"]).copy()
    if x.empty or x[col].nunique() < q:
        return pd.DataFrame()
    try:
        x["_bin"] = pd.qcut(x[col], q=q, duplicates="drop")
    except ValueError:
        return pd.DataFrame()
    rows = []
    for b, g in x.groupby("_bin", observed=True):
        s = stats_group(g)
        s["特征"] = col
        s["分位"] = str(b)
        rows.append(s)
    return pd.DataFrame(rows)


def cat_table(df: pd.DataFrame, col: str) -> pd.DataFrame:
    rows = []
    for k, g in df.dropna(subset=["next_day_ret"]).groupby(col):
        if not str(k).strip() or str(k) == "nan":
            continue
        s = stats_group(g)
        s["特征"] = col
        s["取值"] = str(k)
        rows.append(s)
    return pd.DataFrame(rows).sort_values("均涨%", ascending=False)


def spearman_row(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    rows = []
    for c in cols:
        x = df.dropna(subset=[c, "next_day_ret"])
        if len(x) < 30:
            continue
        rho = float(x[c].corr(x["next_day_ret"], method="spearman"))
        rows.append({"特征": c, "Spearman": rho, "n": len(x)})
    return pd.DataFrame(rows).sort_values("Spearman", key=lambda s: s.abs(), ascending=False)


def eval_rule(df: pd.DataFrame, mask: pd.Series, name: str) -> dict:
    sel = df.loc[mask]
    rest = df.loc[~mask]
    if len(sel) < 20:
        return {"规则": name, "n": len(sel), "均涨%": np.nan}
    s = stats_group(sel)
    r = stats_group(rest) if len(rest) >= 20 else {k: np.nan for k in s}
    return {
        "规则": name,
        "选中n": s["n"],
        "选中均涨%": s["均涨%"],
        "选中胜率%": s["胜率%"],
        "选中涨停率%": s["涨停率%"],
        "选中大亏率%": s["大亏率%"],
        "其余n": r.get("n", np.nan),
        "其余均涨%": r.get("均涨%", np.nan),
        "超额(均pp)": s["均涨%"] - (r.get("均涨%") or 0),
        "覆盖率%": s["n"] / len(df) * 100,
    }


def main() -> None:
    df = load_pool()
    print(f"有效样本 {len(df)}，日数 {df['seal_date'].nunique()}")

    # 1) 数值相关
    num_cols = ["amt", "log_amt", "hardness", "rush", "hard_x_rush", "amt_x_hard",
                "stability_n", "trend_n", "confidence_n", "rating_score"]
    corr = spearman_row(df, num_cols)
    print("\n======== Spearman vs 次日涨幅 ========")
    print(corr.to_string(index=False))

    # 2) 分位
    q_parts = []
    for c in ["amt", "hardness", "rush", "hard_x_rush", "amt_x_hard"]:
        t = quantile_table(df, c, 5)
        if len(t):
            q_parts.append(t)
            print(f"\n======== {c} 五分位 ========")
            print(t.to_string(index=False))
    q_all = pd.concat(q_parts, ignore_index=True) if q_parts else pd.DataFrame()

    # 3) 类别
    cat_parts = []
    for c in ["stability", "trend", "confidence"]:
        t = cat_table(df, c)
        if len(t):
            cat_parts.append(t)
            print(f"\n======== {c} ========")
            print(t.to_string(index=False))
    cat_all = pd.concat(cat_parts, ignore_index=True) if cat_parts else pd.DataFrame()

    # 4) 交叉：稳定性 × 趋势
    cross_rows = []
    for stab, g1 in df.groupby("stability"):
        if not stab:
            continue
        for trend, g2 in g1.groupby("trend"):
            if not trend or len(g2) < 30:
                continue
            s = stats_group(g2)
            s["稳定性"] = stab
            s["趋势"] = trend
            cross_rows.append(s)
    cross = pd.DataFrame(cross_rows).sort_values("均涨%", ascending=False)
    print("\n======== 稳定性×趋势 ========")
    print(cross.to_string(index=False))

    # 5) 候选规则扫描（简单可解释）
    rules = []
    # 金额门槛
    for thr in [0.2, 0.3, 0.5, 0.8, 1.0, 1.5, 2.0, 3.0]:
        rules.append(eval_rule(df, df["amt"] >= thr, f"封单金额≥{thr}亿"))
    # 硬度
    for thr in [1, 2, 5, 10, 20, 50, 100]:
        rules.append(eval_rule(df, df["hardness"] >= thr, f"硬度≥{thr}%"))
    # 抢筹
    for thr in [0.1, 0.3, 0.5, 1.0, 2.0, 5.0]:
        rules.append(eval_rule(df, df["rush"] >= thr, f"抢筹≥{thr}%"))
    # 组合
    rules.append(eval_rule(df, df["stable_strong"], "极度稳定+持续加强"))
    rules.append(eval_rule(df, df["stable_ok"], "稳定≥平稳 且 趋势≥平稳"))
    rules.append(eval_rule(df, (~df["volatile"]) & (~df["weakening"]), "非剧烈波动 且 非持续减弱"))
    rules.append(eval_rule(df, df["volatile"] & df["weakening"], "剧烈波动+持续减弱"))
    rules.append(eval_rule(df, (df["amt"] >= 0.5) & (df["stability_n"] >= 3), "金额≥0.5 且 极度稳定"))
    rules.append(eval_rule(df, (df["amt"] >= 0.5) & (df["trend_n"] >= 3), "金额≥0.5 且 持续加强"))
    rules.append(eval_rule(df, (df["amt"] >= 0.3) & (df["stability_n"] >= 3) & (df["trend_n"] >= 3),
                           "金额≥0.3 + 极稳 + 加强"))
    rules.append(eval_rule(df, (df["amt"] >= 1.0) & (df["stability_n"] >= 2), "金额≥1.0 且 稳定≥平稳"))
    rules.append(eval_rule(df, (df["hardness"] >= 10) & (df["amt"] >= 0.3), "硬度≥10% 且 金额≥0.3"))
    rules.append(eval_rule(df, (df["rush"] >= 1.0) & (df["amt"] >= 0.3), "抢筹≥1% 且 金额≥0.3"))
    rules.append(eval_rule(df, (df["hardness"] >= 5) & (df["rush"] >= 0.5) & (df["amt"] >= 0.3),
                           "硬≥5 + 抢≥0.5 + 金额≥0.3"))
    rules.append(eval_rule(df, (df["amt"] >= 0.5) & (~df["volatile"]) & (df["trend_n"] >= 2),
                           "金额≥0.5 + 非剧波 + 趋势非减弱"))
    rules.append(eval_rule(df, (df["amt"] >= 0.8) & (df["stability_n"] >= 3) & (df["trend_n"] >= 2),
                           "金额≥0.8 + 极稳 + 趋势≥平稳"))
    # 置信
    rules.append(eval_rule(df, df["confidence_n"] >= 2, "置信≥中"))
    rules.append(eval_rule(df, df["confidence_n"] >= 3, "置信=高"))
    # 基线：原评级
    rules.append(eval_rule(df, df["rating_score"] >= 4, "【基线】原评级≥强势"))
    rules.append(eval_rule(df, df["rating_score"] <= 2, "【基线】原评级≤弱势"))

    rule_df = pd.DataFrame(rules).dropna(subset=["选中均涨%"])
    rule_df = rule_df.sort_values(["选中均涨%", "超额(均pp)"], ascending=False)
    print("\n======== 规则扫描 Top20（按选中均涨） ========")
    print(rule_df.head(20).to_string(index=False))

    # 限制覆盖率不要太极端：选中 5%~40%，比基线更好
    baseline = rule_df[rule_df["规则"] == "【基线】原评级≥强势"]
    base_mean = float(baseline["选中均涨%"].iloc[0]) if len(baseline) else 4.5
    base_n = int(baseline["选中n"].iloc[0]) if len(baseline) else 300
    candid = rule_df[
        (rule_df["覆盖率%"] >= 3)
        & (rule_df["覆盖率%"] <= 40)
        & (~rule_df["规则"].str.startswith("【基线】"))
    ].copy()
    candid["vs基线均涨"] = candid["选中均涨%"] - base_mean
    candid = candid.sort_values("选中均涨%", ascending=False)
    print(f"\n======== 覆盖率3%~40% 候选（基线强势均涨={base_mean:.2f}% n={base_n}） ========")
    print(candid.head(15).to_string(index=False))

    # 6) 按月稳定性：挑几条最好规则
    top_rules = [
        "极度稳定+持续加强",
        "金额≥0.3 + 极稳 + 加强",
        "金额≥0.5 且 持续加强",
        "金额≥0.8 + 极稳 + 趋势≥平稳",
        "金额≥0.5 + 非剧波 + 趋势非减弱",
        "【基线】原评级≥强势",
    ]
    # 确保这些规则在 rule_df 里；若没有则重算
    month_rows = []
    rule_masks = {
        "极度稳定+持续加强": df["stable_strong"],
        "金额≥0.3 + 极稳 + 加强": (df["amt"] >= 0.3) & (df["stability_n"] >= 3) & (df["trend_n"] >= 3),
        "金额≥0.5 且 持续加强": (df["amt"] >= 0.5) & (df["trend_n"] >= 3),
        "金额≥0.8 + 极稳 + 趋势≥平稳": (df["amt"] >= 0.8) & (df["stability_n"] >= 3) & (df["trend_n"] >= 2),
        "金额≥0.5 + 非剧波 + 趋势非减弱": (df["amt"] >= 0.5) & (~df["volatile"]) & (df["trend_n"] >= 2),
        "【基线】原评级≥强势": df["rating_score"] >= 4,
        "全市场(对照)": pd.Series(True, index=df.index),
    }
    for month, g in df.groupby(df["seal_date"].str[:6]):
        for name, mask_all in rule_masks.items():
            mask = mask_all.reindex(g.index).fillna(False)
            sub = g.loc[mask]
            if len(sub) < 5:
                continue
            s = stats_group(sub)
            month_rows.append({"月份": month, "规则": name, **s})
    by_month = pd.DataFrame(month_rows)
    print("\n======== 关键规则按月 ========")
    if len(by_month):
        pivot = by_month.pivot_table(index="规则", columns="月份", values="均涨%", aggfunc="first")
        print(pivot.to_string())

    # 写文件
    with pd.ExcelWriter(OUT, engine="openpyxl") as w:
        df.to_excel(w, sheet_name="明细", index=False)
        corr.to_excel(w, sheet_name="Spearman", index=False)
        q_all.to_excel(w, sheet_name="数值五分位", index=False)
        cat_all.to_excel(w, sheet_name="类别均值", index=False)
        cross.to_excel(w, sheet_name="稳定x趋势", index=False)
        rule_df.to_excel(w, sheet_name="规则扫描", index=False)
        candid.to_excel(w, sheet_name="候选规则", index=False)
        by_month.to_excel(w, sheet_name="关键规则按月", index=False)
    print(f"\n已写出: {OUT}")


if __name__ == "__main__":
    main()
