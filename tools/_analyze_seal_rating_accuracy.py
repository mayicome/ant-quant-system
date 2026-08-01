# -*- coding: utf-8 -*-
"""批量评估封单评级准确度：封单结构_YYYYMMDD + daily_cache 次日收益。"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(r"d:\蚂蚁量化系统")
HIST = ROOT / "history_data"
DAILY = ROOT / "data" / "daily_cache"
OUT = HIST / f"封单评级准确度评估_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"

RATING_ORDER = {
    "🔴 虚封高危": 1,
    "🟠 弱势封板": 2,
    "🟡 中等封板": 3,
    "🟢 强势封板": 4,
    "🔥 超强极致封板": 5,
}

# 反向映射：分值 -> 简称（打印用）
RATING_SHORT = {
    1: "虚封高危",
    2: "弱势封板",
    3: "中等封板",
    4: "强势封板",
    5: "超强极致",
}


def normalize_code(v: object) -> str:
    s = str(v or "").strip().upper()
    # 去零宽字符等
    s = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", s)
    m = re.search(r"(\d{6})", s)
    return m.group(1) if m else ""


def full_code_path(code6: str) -> Optional[Path]:
    if not code6:
        return None
    for suf in (".SZ", ".SH", ".BJ"):
        p = DAILY / f"{code6}{suf}.csv"
        if p.is_file():
            return p
    # 兜底：任意匹配
    hits = list(DAILY.glob(f"{code6}.*.csv"))
    return hits[0] if hits else None


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


_daily_cache: Dict[str, pd.DataFrame] = {}


def load_daily(code6: str) -> Optional[pd.DataFrame]:
    if code6 in _daily_cache:
        return _daily_cache[code6]
    p = full_code_path(code6)
    if p is None:
        _daily_cache[code6] = None  # type: ignore[assignment]
        return None
    df = pd.read_csv(p)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.normalize()
    df = df.dropna(subset=["date"]).sort_values("date")
    for c in ("open", "high", "low", "close", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    _daily_cache[code6] = df
    return df


def next_day_metrics(
    code6: str, seal_ymd: str
) -> Tuple[Optional[float], Optional[bool], Optional[str]]:
    """返回 (次日收盘涨幅%, 次日是否涨停, 次日YYYYMMDD)。"""
    df = load_daily(code6)
    if df is None or df.empty:
        return None, None, None
    seal_dt = pd.Timestamp(datetime.strptime(seal_ymd, "%Y%m%d"))
    # 封板日收盘（允许文件日与K线日略有偏差：取 <= seal 的最近一根）
    before = df[df["date"] <= seal_dt]
    if before.empty:
        return None, None, None
    seal_row = before.iloc[-1]
    seal_close = float(seal_row["close"] or 0)
    if seal_close <= 0:
        return None, None, None
    after = df[df["date"] > seal_row["date"]]
    if after.empty:
        return None, None, None
    nxt = after.iloc[0]
    nxt_close = float(nxt["close"] or 0)
    if nxt_close <= 0:
        return None, None, None
    ret = (nxt_close / seal_close - 1.0) * 100.0
    lim = is_limit_up(seal_close, nxt_close, code6)
    nxt_ymd = nxt["date"].strftime("%Y%m%d")
    return ret, lim, nxt_ymd


def list_seal_files() -> List[Tuple[str, Path]]:
    out: List[Tuple[str, Path]] = []
    for p in HIST.glob("封单结构_*.xlsx"):
        name = p.name
        if "含次日" in name or "滚动" in name or "评估" in name or "参数" in name:
            continue
        if name.startswith("~$"):
            continue
        m = re.search(r"(\d{8})", name)
        if not m:
            continue
        out.append((m.group(1), p))
    out.sort(key=lambda x: x[0])
    return out


def find_col(df: pd.DataFrame, aliases: List[str]) -> Optional[str]:
    """中英文列名兼容：精确/包含匹配。"""
    cols = list(df.columns)
    lower = {str(c).strip().lower(): c for c in cols}
    for a in aliases:
        k = a.strip().lower()
        if k in lower:
            return lower[k]
    for a in aliases:
        k = a.strip().lower()
        for c in cols:
            if k in str(c).strip().lower():
                return c
    return None


def resolve_rating(raw_rating: object, raw_score: object) -> Tuple[Optional[str], Optional[int]]:
    """优先用评级分值；否则从中英文评级文案映射。"""
    score = pd.to_numeric(raw_score, errors="coerce")
    if pd.notna(score):
        si = int(score)
        if si in RATING_SHORT:
            # 反查标准文案
            for k, v in RATING_ORDER.items():
                if v == si:
                    return k, si
            return RATING_SHORT[si], si
    rating = str(raw_rating or "").strip()
    if not rating or rating.lower() in ("nan", "none"):
        return None, None
    if rating in RATING_ORDER:
        return rating, RATING_ORDER[rating]
    for k, v in RATING_ORDER.items():
        # 去 emoji 后匹配：虚封高危 / 强势封板 等
        bare = k[2:] if len(k) > 2 else k
        if bare in rating or rating in k or bare.lower() in rating.lower():
            return k, v
    # 英文简写
    en = {
        "danger": 1,
        "weak": 2,
        "medium": 3,
        "mid": 3,
        "strong": 4,
        "extreme": 5,
        "super": 5,
    }
    rl = rating.lower()
    for key, v in en.items():
        if key in rl:
            for k, vv in RATING_ORDER.items():
                if vv == v:
                    return k, v
    return None, None


def load_all() -> pd.DataFrame:
    rows: List[dict] = []
    files = list_seal_files()
    print(f"封单结构文件: {len(files)} 份  ({files[0][0]} ~ {files[-1][0]})")
    n_zh = n_en = 0
    for i, (seal_ymd, path) in enumerate(files, 1):
        try:
            raw = pd.read_excel(path)
        except Exception as e:
            print(f"[跳过] {path.name}: {e}")
            continue
        code_col = find_col(raw, ["股票代码", "代码", "code"])
        name_col = find_col(raw, ["股票名称", "名称", "name"])
        rating_col = find_col(raw, ["封单评级", "order_rating", "评级"])
        score_col = find_col(raw, ["评级分值", "rating_score"])
        conf_col = find_col(raw, ["置信度", "confidence_tag", "置信"])
        amt_col = find_col(raw, ["收盘封单金额(亿)", "close_order_amount_yi", "封单金额"])
        if code_col is None or (rating_col is None and score_col is None):
            print(f"[跳过] {path.name}: 缺代码/评级列 cols={list(raw.columns)[:8]}")
            continue
        is_en = any(str(c).isascii() and "_" in str(c) for c in raw.columns)
        if is_en:
            n_en += 1
        else:
            n_zh += 1
        before = len(rows)
        for _, r in raw.iterrows():
            code = normalize_code(r[code_col])
            if not code:
                continue
            rating_raw = r[rating_col] if rating_col is not None else None
            score_raw = r[score_col] if score_col is not None else None
            rating, score = resolve_rating(rating_raw, score_raw)
            if score is None:
                continue
            ret, lim, nxt = next_day_metrics(code, seal_ymd)
            rows.append(
                {
                    "seal_date": seal_ymd,
                    "next_date": nxt,
                    "code": code,
                    "name": str(r[name_col]).strip() if name_col else "",
                    "seal_rating": rating or "",
                    "rating_score": score,
                    "confidence": str(r[conf_col]).strip() if conf_col else "",
                    "close_amt_yi": pd.to_numeric(r[amt_col], errors="coerce")
                    if amt_col
                    else np.nan,
                    "next_day_ret": ret,
                    "next_day_limit_up": lim,
                    "schema": "en" if is_en else "zh",
                }
            )
        added = len(rows) - before
        if i % 10 == 0 or i == len(files):
            print(f"  已处理 {i}/{len(files)} … 累计行 {len(rows)}（本文件 +{added}）")
    print(f"文件格式: 中文列 {n_zh} 份，英文列 {n_en} 份")
    return pd.DataFrame(rows)


def summarize(df: pd.DataFrame, label: str) -> pd.DataFrame:
    """按评级汇总。"""
    rows = []
    for score in sorted(df["rating_score"].unique()):
        sub = df[df["rating_score"] == score]
        rets = sub["next_day_ret"].dropna()
        lims = sub["next_day_limit_up"].dropna()
        rows.append(
            {
                "样本": label,
                "评级分": int(score),
                "评级": RATING_SHORT.get(int(score), str(score)),
                "n": len(sub),
                "有效次日n": int(rets.shape[0]),
                "次日均涨%": float(rets.mean()) if len(rets) else np.nan,
                "次日中位%": float(rets.median()) if len(rets) else np.nan,
                "胜率(>0)%": float((rets > 0).mean() * 100) if len(rets) else np.nan,
                "胜率(>-1)%": float((rets > -1).mean() * 100) if len(rets) else np.nan,
                "大亏率(<-5)%": float((rets < -5).mean() * 100) if len(rets) else np.nan,
                "次日涨停率%": float(lims.mean() * 100) if len(lims) else np.nan,
            }
        )
    return pd.DataFrame(rows)


def spearman(df: pd.DataFrame) -> float:
    x = df.dropna(subset=["rating_score", "next_day_ret"])
    if len(x) < 10:
        return float("nan")
    return float(x["rating_score"].corr(x["next_day_ret"], method="spearman"))


def high_vs_low(df: pd.DataFrame) -> dict:
    """高评级(4-5) vs 低评级(1-2)。"""
    hi = df[df["rating_score"] >= 4]["next_day_ret"].dropna()
    lo = df[df["rating_score"] <= 2]["next_day_ret"].dropna()
    mid = df[df["rating_score"] == 3]["next_day_ret"].dropna()
    return {
        "高(强势+)n": len(hi),
        "高均%": float(hi.mean()) if len(hi) else np.nan,
        "高胜率%": float((hi > 0).mean() * 100) if len(hi) else np.nan,
        "高涨停率%": float(
            df.loc[df["rating_score"] >= 4, "next_day_limit_up"].dropna().mean() * 100
        )
        if len(df[df["rating_score"] >= 4])
        else np.nan,
        "低(弱势-)n": len(lo),
        "低均%": float(lo.mean()) if len(lo) else np.nan,
        "低胜率%": float((lo > 0).mean() * 100) if len(lo) else np.nan,
        "低涨停率%": float(
            df.loc[df["rating_score"] <= 2, "next_day_limit_up"].dropna().mean() * 100
        )
        if len(df[df["rating_score"] <= 2])
        else np.nan,
        "中n": len(mid),
        "中均%": float(mid.mean()) if len(mid) else np.nan,
        "高低差(均pp)": (float(hi.mean()) - float(lo.mean())) if len(hi) and len(lo) else np.nan,
    }


def month_bucket(seal_ymd: str) -> str:
    return seal_ymd[:6]


def main() -> None:
    pooled = load_all()
    if pooled.empty:
        print("无样本")
        return

    valid = pooled.dropna(subset=["next_day_ret"]).copy()
    print(
        f"\n合并完成: 总行 {len(pooled)}，有次日收益 {len(valid)}，"
        f"封板日 {valid['seal_date'].nunique()}，"
        f"缺失次日 {len(pooled) - len(valid)}"
    )

    # 全样本
    by_rating = summarize(valid, "全样本")
    rho = spearman(valid)
    hl = high_vs_low(valid)

    # 质控：封单金额>=0.3亿
    qc = valid[valid["close_amt_yi"].fillna(0) >= 0.3]
    by_rating_qc = summarize(qc, "封单≥0.3亿")
    rho_qc = spearman(qc)
    hl_qc = high_vs_low(qc)

    # 置信度中/高
    conf = valid[valid["confidence"].astype(str).str.contains("中|高", regex=True)]
    by_rating_conf = summarize(conf, "置信≥中") if len(conf) else pd.DataFrame()
    rho_conf = spearman(conf) if len(conf) >= 10 else float("nan")

    # 按月
    month_rows = []
    for m, g in valid.groupby(valid["seal_date"].map(month_bucket)):
        h = high_vs_low(g)
        month_rows.append(
            {
                "月份": m,
                "n": len(g),
                "日数": g["seal_date"].nunique(),
                "Spearman": spearman(g),
                **{k: h[k] for k in ("高均%", "低均%", "高低差(均pp)", "高胜率%", "低胜率%", "高涨停率%", "低涨停率%")},
            }
        )
    by_month = pd.DataFrame(month_rows).sort_values("月份")

    # 打印
    print("\n======== 全样本：按评级 ========")
    print(by_rating.to_string(index=False))
    print(f"\nSpearman(评级分, 次日涨幅) = {rho:+.3f}")
    print("高 vs 低:", {k: (round(v, 2) if isinstance(v, float) else v) for k, v in hl.items()})

    print("\n======== 质控 封单≥0.3亿 ========")
    print(by_rating_qc.to_string(index=False))
    print(f"\nSpearman = {rho_qc:+.3f}")
    print("高 vs 低:", {k: (round(v, 2) if isinstance(v, float) else v) for k, v in hl_qc.items()})

    if len(by_rating_conf):
        print("\n======== 置信度≥中 ========")
        print(by_rating_conf.to_string(index=False))
        print(f"Spearman = {rho_conf:+.3f}")

    print("\n======== 按月（高/低对比） ========")
    print(by_month.to_string(index=False))

    # 中英文列分段对照
    schema_rows = []
    if "schema" in valid.columns:
        for sch, g in valid.groupby("schema"):
            h = high_vs_low(g)
            schema_rows.append(
                {
                    "格式": "英文列" if sch == "en" else "中文列",
                    "n": len(g),
                    "日数": g["seal_date"].nunique(),
                    "日期起": g["seal_date"].min(),
                    "日期止": g["seal_date"].max(),
                    "Spearman": spearman(g),
                    "高均%": h["高均%"],
                    "低均%": h["低均%"],
                    "高低差(均pp)": h["高低差(均pp)"],
                    "高胜率%": h["高胜率%"],
                    "低胜率%": h["低胜率%"],
                }
            )
        by_schema = pd.DataFrame(schema_rows)
        print("\n======== 中英文列分段 ========")
        print(by_schema.to_string(index=False))
    else:
        by_schema = pd.DataFrame()

    # 单调性检查
    means = by_rating.set_index("评级分")["次日均涨%"]
    mono_ok = all(means.iloc[i] <= means.iloc[i + 1] + 0.5 for i in range(len(means) - 1))
    print("\n======== 结论草稿 ========")
    print(f"评级越高次日均涨是否大致单调上升: {'是' if mono_ok else '否（有倒挂）'}")
    print(f"高低差(全样本) = {hl['高低差(均pp)']:+.2f}pp")
    print(f"高低差(封单≥0.3亿) = {hl_qc['高低差(均pp)']:+.2f}pp")

    # 写 Excel
    with pd.ExcelWriter(OUT, engine="openpyxl") as w:
        valid.to_excel(w, sheet_name="明细", index=False)
        by_rating.to_excel(w, sheet_name="全样本_按评级", index=False)
        by_rating_qc.to_excel(w, sheet_name="质控03亿_按评级", index=False)
        if len(by_rating_conf):
            by_rating_conf.to_excel(w, sheet_name="置信中高_按评级", index=False)
        by_month.to_excel(w, sheet_name="按月", index=False)
        if len(by_schema):
            by_schema.to_excel(w, sheet_name="中英文分段", index=False)
        pd.DataFrame(
            [
                {"指标": "全样本Spearman", "值": rho},
                {"指标": "质控Spearman", "值": rho_qc},
                {"指标": "置信Spearman", "值": rho_conf},
                {"指标": "全样本高低差pp", "值": hl["高低差(均pp)"]},
                {"指标": "质控高低差pp", "值": hl_qc["高低差(均pp)"]},
                {"指标": "有效样本n", "值": len(valid)},
                {"指标": "封板日数", "值": valid["seal_date"].nunique()},
            ]
        ).to_excel(w, sheet_name="总览", index=False)
    print(f"\n已写出: {OUT}")


if __name__ == "__main__":
    main()
