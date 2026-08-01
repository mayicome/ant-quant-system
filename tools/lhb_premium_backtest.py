# -*- coding: utf-8 -*-
"""龙虎榜「次日溢价预测」历史回测。

读取 history_data（含存档）下全部「龙虎榜解析_YYYYMMDD.xlsx」，
对照 daily_cache 次一交易日 OHLC，评估预测分层与命中率。

用法：
  python tools/lhb_premium_backtest.py
"""
from __future__ import annotations

import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.daily_cache_reader import load_daily_from_cache  # noqa: E402
from lhb_analysis_gui import compute_lhb_strength_and_premium, extract_lhb_seat_amounts  # noqa: E402

HIST = ROOT / "history_data"
OUT = HIST / "龙虎榜次日溢价预测回测.xlsx"

# 新文案优先；旧五档文案映射到三档，便于读历史 xlsx
_TIER_MAP = {
    "强": "强",
    "中": "中",
    "弱": "弱",
    "极高溢价": "强",
    "高溢价": "中",
    "中等溢价": "中",
    "低溢价": "弱",
    "负溢价": "弱",
}
_TIER_ORDER = ["强", "中", "弱"]


def _norm_header(c: object) -> str:
    return re.sub(r"\s+", "", str(c or "").replace("\n", ""))


def _norm_code(v: object) -> str:
    s = re.sub(r"\D", "", str(v or ""))
    if not s:
        return ""
    if len(s) < 6:
        s = s.zfill(6)
    return s[-6:]


def _tier_from_premium(text: object) -> str:
    s = str(text or "")
    for k, v in _TIER_MAP.items():
        if k in s:
            return v
    return "未知"


def list_lhb_files() -> List[Path]:
    cands: List[Path] = []
    for base in (HIST, HIST / "存档"):
        if not base.is_dir():
            continue
        for p in base.rglob("龙虎榜解析_*.xlsx"):
            if p.name.startswith("~$"):
                continue
            cands.append(p)
    # 同日只留一份（优先非存档）
    by_day: Dict[str, Path] = {}
    for p in sorted(cands, key=lambda x: (0 if "存档" not in x.parts else 1, str(x))):
        m = re.search(r"(\d{8})", p.name)
        if not m:
            continue
        day = m.group(1)
        if day not in by_day:
            by_day[day] = p
    return [by_day[k] for k in sorted(by_day.keys())]


def read_summary(path: Path) -> Optional[pd.DataFrame]:
    try:
        df = pd.read_excel(path, sheet_name=0)
    except Exception:
        return None
    df = df.copy()
    df.columns = [_norm_header(c) for c in df.columns]
    need = ["代码", "次日溢价预测"]
    if any(c not in df.columns for c in need):
        return None
    if "主力强度(0-100)" not in df.columns and "主力强度" in df.columns:
        df = df.rename(columns={"主力强度": "主力强度(0-100)"})
    return df


def next_day_rets(code6: str, lhb_ymd: str) -> Optional[dict]:
    df = load_daily_from_cache(code6)
    if df is None or df.empty:
        return None
    seal_dt = datetime.strptime(lhb_ymd, "%Y%m%d").date()
    before = df[df["date"] <= seal_dt]
    if before.empty:
        return None
    base_close = float(before.iloc[-1]["close"] or 0)
    if base_close <= 0:
        return None
    after = df[df["date"] > before.iloc[-1]["date"]]
    if after.empty:
        return None
    nxt = after.iloc[0]
    o, h, l, c = [float(nxt[x] or 0) for x in ("open", "high", "low", "close")]
    if min(o, h, l, c) <= 0:
        return None
    return {
        "次日": nxt["date"].strftime("%Y-%m-%d") if hasattr(nxt["date"], "strftime") else str(nxt["date"]),
        "基准收盘": round(base_close, 4),
        "开盘涨跌%": round((o / base_close - 1) * 100, 3),
        "最高涨跌%": round((h / base_close - 1) * 100, 3),
        "最低涨跌%": round((l / base_close - 1) * 100, 3),
        "收盘涨跌%": round((c / base_close - 1) * 100, 3),
    }


def hit_rule(tier: str, open_ret: float, high_ret: float, close_ret: float) -> Tuple[bool, str]:
    """按文案语义的可验证命中规则。"""
    if tier == "强":
        return open_ret > 0, "次日高开(开盘>0%)"
    if tier == "中":
        return abs(open_ret) <= 2.0, "震荡(|开|≤2%)"
    if tier == "弱":
        return open_ret < 0, "确实低开(开盘<0%)"
    return False, "未知档"


def build_detail(files: List[Path]) -> pd.DataFrame:
    rows = []
    for path in files:
        m = re.search(r"(\d{8})", path.name)
        if not m:
            continue
        lhb_ymd = m.group(1)
        summary = read_summary(path)
        if summary is None or summary.empty:
            continue
        for _, r in summary.iterrows():
            code = _norm_code(r.get("代码"))
            if not code:
                continue
            # 用席位特征按现行算法重算预测（历史 xlsx 里可能仍是旧文案）
            jg_cnt, jg_buy, jg_net, bx_buy, bx_net, yz_net = extract_lhb_seat_amounts(r)
            score, prem = compute_lhb_strength_and_premium(
                jg_cnt=jg_cnt,
                jg_buy=jg_buy,
                jg_net=jg_net,
                bx_buy=bx_buy,
                bx_net=bx_net,
                yz_net=yz_net,
            )
            tier = _tier_from_premium(prem)
            rets = next_day_rets(code, lhb_ymd)
            base = {
                "龙虎榜日": lhb_ymd,
                "代码": code,
                "名称": str(r.get("名称") or ""),
                "主力强度": float(score),
                "预测档": tier,
                "次日溢价预测": prem,
                "文件": path.name,
            }
            if rets is None:
                base["有效"] = False
                rows.append(base)
                continue
            hit, rule = hit_rule(
                tier, rets["开盘涨跌%"], rets["最高涨跌%"], rets["收盘涨跌%"]
            )
            base.update(rets)
            base["有效"] = True
            base["命中"] = bool(hit)
            base["命中规则"] = rule
            rows.append(base)
    return pd.DataFrame(rows)


def summarize(detail: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame, str]:
    valid = detail[detail["有效"] == True].copy()  # noqa: E712
    tier_rows = []
    for tier in _TIER_ORDER:
        g = valid[valid["预测档"] == tier]
        if g.empty:
            tier_rows.append(
                {
                    "预测档": tier,
                    "样本数": 0,
                    "命中率%": np.nan,
                    "开盘均%": np.nan,
                    "最高均%": np.nan,
                    "收盘均%": np.nan,
                    "高开占比%": np.nan,
                    "低开占比%": np.nan,
                    "收涨占比%": np.nan,
                    "命中规则": "",
                }
            )
            continue
        hit_rate = (g["命中"] == True).mean() * 100  # noqa: E712
        tier_rows.append(
            {
                "预测档": tier,
                "样本数": int(len(g)),
                "命中率%": round(hit_rate, 1),
                "开盘均%": round(g["开盘涨跌%"].mean(), 2),
                "最高均%": round(g["最高涨跌%"].mean(), 2),
                "收盘均%": round(g["收盘涨跌%"].mean(), 2),
                "高开占比%": round((g["开盘涨跌%"] > 0).mean() * 100, 1),
                "低开占比%": round((g["开盘涨跌%"] < 0).mean() * 100, 1),
                "收涨占比%": round((g["收盘涨跌%"] > 0).mean() * 100, 1),
                "命中规则": str(g["命中规则"].iloc[0]),
            }
        )
    by_tier = pd.DataFrame(tier_rows)

    bins = [-np.inf, 33, 66, np.inf]
    labels = ["<33弱", "33-65中", "≥66强"]
    valid = valid.copy()
    valid["强度桶"] = pd.cut(valid["主力强度"], bins=bins, labels=labels, right=False)
    score_rows = []
    for lab in labels:
        g = valid[valid["强度桶"] == lab]
        if g.empty:
            continue
        score_rows.append(
            {
                "强度桶": lab,
                "样本数": int(len(g)),
                "开盘均%": round(g["开盘涨跌%"].mean(), 2),
                "最高均%": round(g["最高涨跌%"].mean(), 2),
                "收盘均%": round(g["收盘涨跌%"].mean(), 2),
                "高开占比%": round((g["开盘涨跌%"] > 0).mean() * 100, 1),
                "低开占比%": round((g["开盘涨跌%"] < 0).mean() * 100, 1),
            }
        )
    by_score = pd.DataFrame(score_rows)

    # 一句话
    hi = valid[valid["预测档"] == "强"]
    lo = valid[valid["预测档"] == "弱"]
    parts = [f"有效样本 {len(valid)} / 文件日 {valid['龙虎榜日'].nunique()}"]
    if len(hi) and len(lo):
        parts.append(
            f"开盘均：强 {hi['开盘涨跌%'].mean():+.2f}% vs 弱 {lo['开盘涨跌%'].mean():+.2f}%"
        )
        parts.append(
            f"高开占比：强 {(hi['开盘涨跌%']>0).mean()*100:.0f}% vs 弱 {(lo['开盘涨跌%']>0).mean()*100:.0f}%"
        )
        ok = hi["开盘涨跌%"].mean() > lo["开盘涨跌%"].mean() + 0.3
        verdict = "分层有效（强档次日开盘更强）" if ok else "分层偏弱（强/弱开盘差不明显）"
    else:
        verdict = "样本不足，难以判断分层"
    headline = verdict + "。" + "；".join(parts)
    return by_tier, by_score, headline


def main() -> int:
    files = list_lhb_files()
    if not files:
        print("未找到龙虎榜解析_*.xlsx")
        return 1
    print(f"找到 {len(files)} 个龙虎榜文件：{files[0].name} … {files[-1].name}")
    detail = build_detail(files)
    by_tier, by_score, headline = summarize(detail)
    valid_n = int((detail["有效"] == True).sum())  # noqa: E712

    how = pd.DataFrame(
        [
            {"项目": "算法", "说明": "lhb_analysis_gui.compute_lhb_strength_and_premium（按席位特征重算，非旧版xlsx文案）"},
            {"项目": "目的", "说明": "回测「次日溢价预测」文案是否与次日真实开高低收相符"},
            {"项目": "基准价", "说明": "龙虎榜日（及之前最近有K线日）收盘价"},
            {"项目": "表现日", "说明": "基准日后第一个有K线的交易日"},
            {"项目": "强命中", "说明": "开盘涨跌%>0（高开）"},
            {"项目": "中命中", "说明": "|开盘涨跌%|≤2%（震荡）"},
            {"项目": "弱命中", "说明": "开盘涨跌%<0（低开风险兑现）"},
            {"项目": "一句话", "说明": headline},
        ]
    )

    HIST.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(OUT, engine="openpyxl") as w:
        how.to_excel(w, sheet_name="说明", index=False)
        by_tier.to_excel(w, sheet_name="分档命中", index=False)
        by_score.to_excel(w, sheet_name="强度分层", index=False)
        detail.sort_values(["龙虎榜日", "主力强度"], ascending=[True, False]).to_excel(
            w, sheet_name="个股明细", index=False
        )

    print("\n【一句话】", headline)
    print("\n【分档命中】")
    print(by_tier.to_string(index=False))
    print("\n【强度分层】")
    print(by_score.to_string(index=False))
    print(f"\n有效 {valid_n} / 全部 {len(detail)}")
    print(f"已写出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
