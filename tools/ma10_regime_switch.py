# -*- coding: utf-8 -*-
"""
马总 MA10 · 近窗风格切换打分

用法:
  python tools/ma10_regime_switch.py
  python tools/ma10_regime_switch.py --window 15
  python tools/ma10_regime_switch.py --asof 2026-07-31 --window 12

逻辑:
  - 默认核 CORE: B ∩ 市值≤91亿（6/7月双正交集）
  - 七月风 JULY: 价≤18 或 市值≤54（小票/低价）
  - 六月风 JUNE: 市值170~353 ∩ MA5>MA10（中大盘多头）
  - 黑名单: (MA5≤MA10且MA10≥MA20) 或 RS20>40% —— 永不建议

用最近 N 个选股日的票均收益打分；在 CORE / 七月整包(CORE∪七月) / 六月整包(CORE∪六月)
三者中选近窗日均最高者；若领先次佳不足 edge，则默认 CORE。

规则失效对照（只打分、不进决策）:
  - 仅B: 上MA10 ∩ B − 黑名单（比 CORE 宽，不限市值≤91）
  - 非B: 上MA10 ∩ ¬B − 黑名单
  读法: CORE vs 仅B 看市值门槛；仅B vs 非B 看 B 门是否还在分离收益。
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from datetime import date, datetime
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DIR = ROOT / "history_data" / "马总选股逻辑"
OUT_JSON = DIR / "_ma10_regime_switch.json"
OUT_TXT = DIR / "_ma10_regime_switch.txt"

# thresholds (aligned with prior analysis)
MV_CORE = 91.0
MV_JULY_SMALL = 54.0
PX_JULY = 18.0
MV_JUNE_LO, MV_JUNE_HI = 170.0, 353.0
RS5_B, RS10_B, RS20_B = 0.135, 0.06, 0.25
RS20_BLACK = 0.40
# satellite must beat the other by this much (pct points) to activate
LEAD_EDGE = 0.35

# 与 install_ma_zong_ma10_regime_rules.py 安装的规则名一致
RULE_UI = {
    "CORE_ONLY": "马总-MA10核-CORE",
    "CORE_PLUS_JULY": "马总-MA10核-七月风",
    "CORE_PLUS_JUNE": "马总-MA10核-六月风",
}


def code6(v) -> str:
    try:
        return str(int(float(v))).zfill(6)
    except Exception:
        s = "".join(c for c in str(v or "") if c.isdigit())
        return s.zfill(6)[-6:] if s else ""


def as_bool(s: pd.Series) -> pd.Series:
    if s.dtype == bool:
        return s.fillna(False)
    if s.dtype == object:
        return s.map(lambda x: str(x).strip().lower() in ("1", "true", "yes", "是", "y", "t"))
    return s.fillna(False).astype(bool)


def discover_files() -> list[Path]:
    """Prefer 收盘上MA10 sheets; fall back to raw 按票."""
    above = sorted(DIR.glob("各日选股收益汇总_日线-ma10-单点_按票_*_收盘上MA10.xlsx"))
    if above:
        return above
    return sorted(DIR.glob("各日选股收益汇总_日线-ma10-单点_按票_*.xlsx"))


def load_one(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    df["sel"] = pd.to_datetime(df["选股日"], errors="coerce").dt.date
    df["code"] = df["代码"].map(code6) if "代码" in df.columns else ""
    df["ret"] = pd.to_numeric(df["收益率pct"], errors="coerce")
    for c in [
        "MA5",
        "MA10",
        "MA20",
        "买入成交价",
        "流通市值_亿",
        "近5日RS",
        "近10日RS",
        "近20日RS",
        "sel_close",
    ]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    # if not already filtered to 上MA10, apply when possible
    if "收盘上MA10" not in path.name:
        if "sel_close" in df.columns and "MA10" in df.columns:
            df = df[df["sel_close"] > df["MA10"]].copy()
        elif "MA10" in df.columns and "买入成交价" in df.columns:
            # weak fallback: buy px vs MA10 (not ideal)
            pass

    df = df[df["ret"].notna() & df["sel"].notna()].copy()
    df["px"] = pd.to_numeric(df.get("买入成交价"), errors="coerce")
    df["mv"] = pd.to_numeric(df.get("流通市值_亿"), errors="coerce")
    df["rs5"] = pd.to_numeric(df.get("近5日RS"), errors="coerce")
    df["rs10"] = pd.to_numeric(df.get("近10日RS"), errors="coerce")
    df["rs20"] = pd.to_numeric(df.get("近20日RS"), errors="coerce")
    df["src"] = path.name
    return df


def load_pool(files: list[Path] | None = None) -> pd.DataFrame:
    files = files or discover_files()
    if not files:
        raise FileNotFoundError(f"未找到 ma10 按票文件: {DIR}")
    parts = [load_one(p) for p in files]
    df = pd.concat(parts, ignore_index=True)
    # dedupe same sel+code keep last (newer stamp wins if overlapping)
    df = df.sort_values(["sel", "code"]).drop_duplicates(["sel", "code"], keep="last")
    return df.reset_index(drop=True)


def add_flags(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["B"] = (d["rs5"] > RS5_B) & (d["rs10"] > RS10_B) & (d["rs20"] < RS20_B)
    d["core"] = d["B"] & (d["mv"] <= MV_CORE)
    d["july"] = (d["px"] <= PX_JULY) | (d["mv"] <= MV_JULY_SMALL)
    d["june"] = d["mv"].between(MV_JUNE_LO, MV_JUNE_HI) & (d["MA5"] > d["MA10"])
    d["black"] = ((d["MA5"] <= d["MA10"]) & (d["MA10"] >= d["MA20"])) | (d["rs20"] > RS20_BLACK)
    # actionable sleeves (exclude blacklist)
    d["core_ok"] = d["core"] & ~d["black"]
    d["july_sat"] = d["july"] & ~d["black"]  # satellite candidates (not requiring B)
    d["june_sat"] = d["june"] & ~d["black"]
    # recommended trade sets when satellite active = core ∪ (satellite ∩ above already)
    d["pack_july"] = (d["core_ok"] | (d["july_sat"] & d["B"])) & ~d["black"]
    d["pack_june"] = (d["core_ok"] | d["june_sat"]) & ~d["black"]
    # diagnostic only (rule decay): 仅B / 非B；池子已是上MA10
    d["b_only"] = d["B"] & ~d["black"]
    d["non_b"] = (~d["B"]) & ~d["black"]
    return d


def sleeve_stats(g: pd.DataFrame, mask: pd.Series) -> dict:
    sub = g[mask.fillna(False)]
    if sub.empty:
        return {
            "n": 0,
            "n_days": 0,
            "mean": None,
            "day_mean": None,
            "win": None,
            "pos_days": 0,
        }
    day = sub.groupby("sel")["ret"].mean()
    return {
        "n": int(len(sub)),
        "n_days": int(day.shape[0]),
        "mean": round(float(sub["ret"].mean()), 4),
        "day_mean": round(float(day.mean()), 4),
        "win": round(float((sub["ret"] > 0).mean() * 100), 2),
        "pos_days": int((day > 0).sum()),
    }


def decide(scores: dict, edge: float = LEAD_EDGE) -> dict:
    """三选一：CORE / 七月整包 / 六月整包（按近窗日均，需领先次佳 ≥ edge）。"""
    core = scores["core_ok"]["day_mean"]
    pack_jy = scores["pack_july"]["day_mean"]
    pack_jn = scores["pack_june"]["day_mean"]

    cands: list[tuple[str, float, str]] = []
    if core is not None:
        cands.append(("CORE_ONLY", float(core), "CORE"))
    if pack_jy is not None:
        cands.append(("CORE_PLUS_JULY", float(pack_jy), "七月整包"))
    if pack_jn is not None:
        cands.append(("CORE_PLUS_JUNE", float(pack_jn), "六月整包"))

    rules = {
        "CORE_ONLY": [
            "必选: 上MA10",
            "必选: B = RS5>13.5% 且 RS10>6% 且 RS20<25%",
            "必选: 流通市值 ≤ 91亿",
            "禁止: MA5≤MA10 且 MA10≥MA20",
            "禁止: RS20 > 40%",
        ],
        "CORE_PLUS_JULY": [
            "默认核: B + 市值≤91亿",
            "卫星(七月风): 价≤18 或 市值≤54（建议仍要求 B，避免过宽）",
            "禁止: MA5≤MA10 且 MA10≥MA20；RS20>40%",
        ],
        "CORE_PLUS_JUNE": [
            "默认核: B + 市值≤91亿",
            "卫星(六月风): 市值170~353亿 且 MA5>MA10",
            "禁止: MA5≤MA10 且 MA10≥MA20；RS20>40%",
        ],
    }

    if not cands:
        return {
            "decision": "CORE_ONLY",
            "reason": "近窗无 CORE/整包样本 → 默认 CORE",
            "rules": rules["CORE_ONLY"],
            "core_day_mean": core,
        }

    cands.sort(key=lambda x: x[1], reverse=True)
    best_dec, best_v, best_lab = cands[0]

    if len(cands) == 1:
        decision = best_dec
        reason = f"近窗仅{best_lab}有样本 day={best_v:+.2f}%"
    else:
        second_dec, second_v, second_lab = cands[1]
        lead = best_v - second_v
        if lead < float(edge):
            # 差距不足：不切换风格，落回 CORE（有样本时）
            if core is not None:
                decision = "CORE_ONLY"
                reason = (
                    f"三包接近：最佳{best_lab} {best_v:+.2f}% / 次佳{second_lab} {second_v:+.2f}% "
                    f"差 {lead:.2f}pp < {edge:.2f} → 默认 CORE"
                )
            else:
                decision = best_dec
                reason = (
                    f"{best_lab} {best_v:+.2f}% 略优于{second_lab} {second_v:+.2f}% "
                    f"(差{lead:.2f}<{edge:.2f}且无CORE样本) → 仍选{best_lab}"
                )
        else:
            decision = best_dec
            reason = (
                f"{best_lab}近窗 {best_v:+.2f}% 领先次佳{second_lab} {second_v:+.2f}% "
                f"≥ {edge:.2f}pp"
            )

    return {
        "decision": decision,
        "reason": reason,
        "rules": rules[decision],
        "core_day_mean": core,
    }


def decay_hint(scores: dict, edge: float = LEAD_EDGE) -> str:
    """Human-readable rule-decay read from CORE / 仅B / 非B day means."""
    core = scores["core_ok"]["day_mean"]
    b_only = scores["b_only"]["day_mean"]
    non_b = scores["non_b"]["day_mean"]
    if b_only is None and non_b is None:
        return "近窗无仅B/非B样本，无法判断 B 门是否失效"
    if b_only is not None and non_b is not None:
        gap = b_only - non_b
        if non_b >= b_only:
            return f"警惕: 非B日均 {non_b:+.2f}% ≥ 仅B {b_only:+.2f}% → B 门近窗失去分离力"
        if gap < edge:
            return f"注意: 仅B仅领先非B {gap:+.2f}pp（<{edge:.2f}）→ B 门优势偏弱"
    if core is not None and b_only is not None:
        size_gap = core - b_only
        if size_gap < -edge:
            return f"注意: CORE 日均弱于仅B {abs(size_gap):.2f}pp → 市值≤91 近窗在拖累"
        if (
            abs(size_gap) < 0.20
            and non_b is not None
            and b_only is not None
            and (b_only - non_b) >= edge
        ):
            return (
                f"B 门仍有效（仅B领先非B），但市值门槛近窗几乎无增量"
                f"(CORE-仅B={size_gap:+.2f}pp)"
            )
    if b_only is not None and non_b is not None:
        return f"正常: 仅B {b_only:+.2f}% > 非B {non_b:+.2f}%（差 {b_only - non_b:+.2f}pp）"
    if b_only is not None:
        return f"仅有仅B样本 day={b_only:+.2f}%，缺非B对照"
    return f"仅有非B样本 day={non_b:+.2f}%，缺仅B对照"


def score_window(df: pd.DataFrame, sels: list) -> dict:
    w = df[df["sel"].isin(sels)].copy()
    return {
        "sel_from": str(min(sels)),
        "sel_to": str(max(sels)),
        "n_sel_days": len(sels),
        "n_all": int(len(w)),
        "baseline": sleeve_stats(w, pd.Series(True, index=w.index)),
        "core_ok": sleeve_stats(w, w["core_ok"]),
        "july_sat": sleeve_stats(w, w["july_sat"]),
        "june_sat": sleeve_stats(w, w["june_sat"]),
        "pack_july": sleeve_stats(w, w["pack_july"]),
        "pack_june": sleeve_stats(w, w["pack_june"]),
        "b_only": sleeve_stats(w, w["b_only"]),
        "non_b": sleeve_stats(w, w["non_b"]),
        "black": sleeve_stats(w, w["black"]),
    }


def rolling_report(df: pd.DataFrame, window: int) -> list[dict]:
    sels = sorted(df["sel"].unique())
    rows = []
    for i in range(window - 1, len(sels)):
        win = sels[i - window + 1 : i + 1]
        sc = score_window(df, win)
        dec = decide(sc)
        rows.append(
            {
                "asof": str(sels[i]),
                "decision": dec["decision"],
                "july_day": sc["july_sat"]["day_mean"],
                "june_day": sc["june_sat"]["day_mean"],
                "core_day": sc["core_ok"]["day_mean"],
                "pack_july_day": sc["pack_july"]["day_mean"],
                "pack_june_day": sc["pack_june"]["day_mean"],
                "b_only_day": sc["b_only"]["day_mean"],
                "non_b_day": sc["non_b"]["day_mean"],
                "july_n": sc["july_sat"]["n"],
                "june_n": sc["june_sat"]["n"],
                "core_n": sc["core_ok"]["n"],
                "b_only_n": sc["b_only"]["n"],
                "non_b_n": sc["non_b"]["n"],
            }
        )
    return rows


def parse_asof(s: str | None) -> date | None:
    if not s:
        return None
    return pd.to_datetime(s).date()


def format_report(payload: dict) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append("马总 MA10 · 近窗风格切换")
    lines.append("=" * 60)
    lines.append(f"数据覆盖选股日: {payload['data_from']} → {payload['data_to']}  (共 {payload['n_sel_days']} 天)")
    lines.append(f"评价窗口: 最近 {payload['window']} 个选股日  ({payload['window_from']} → {payload['window_to']})")
    lines.append(f"asof: {payload['asof']}")
    lines.append("")
    lines.append("【近窗得分】日均等权% / 票均% / n")
    for key, lab in [
        ("baseline", "上MA10全样本"),
        ("core_ok", "默认核 CORE (B+市值≤91, 去黑名单)"),
        ("july_sat", "七月风卫星 (价≤18或市值≤54)"),
        ("june_sat", "六月风卫星 (市值170~353且MA5>MA10)"),
        ("pack_july", "CORE并(七月交B)"),
        ("pack_june", "CORE并六月卫星"),
    ]:
        s = payload["scores"][key]
        dm = "NA" if s["day_mean"] is None else f"{s['day_mean']:+.2f}%"
        m = "NA" if s["mean"] is None else f"{s['mean']:+.2f}%"
        lines.append(f"  {lab:36s}  day={dm:>8s}  mean={m:>8s}  n={s['n']:4d}  days={s['n_days']}")
    lines.append("")
    lines.append("【规则失效对照】仅打分、不进决策  |  CORE 含于 仅B  ;  非B 互斥")
    for key, lab in [
        ("b_only", "仅B (上MA10交B去黑名单)"),
        ("non_b", "非B (上MA10交非B去黑名单)"),
    ]:
        s = payload["scores"][key]
        dm = "NA" if s["day_mean"] is None else f"{s['day_mean']:+.2f}%"
        m = "NA" if s["mean"] is None else f"{s['mean']:+.2f}%"
        lines.append(f"  {lab:36s}  day={dm:>8s}  mean={m:>8s}  n={s['n']:4d}  days={s['n_days']}")
    hint = payload.get("decay_hint") or decay_hint(payload["scores"])
    lines.append(f"读法: {hint}")
    lines.append("")
    rule_name = payload.get("rule_ui") or RULE_UI.get(payload["decision"], "")
    lines.append(f"【本周建议】{payload['decision']}")
    if rule_name:
        lines.append(f"选股页勾选: {rule_name}")
    lines.append(f"原因: {payload['reason']}")
    lines.append("执行规则:")
    for r in payload["rules"]:
        lines.append(f"  - {r}")
    if payload.get("history_tail"):
        lines.append("")
        lines.append("【滚动决策轨迹】最近几次 asof → decision")
        for h in payload["history_tail"]:
            lines.append(
                f"  {h['asof']}  {h['decision']:16s}  "
                f"core={h['core_day']}  pack_july={h.get('pack_july_day')}  "
                f"pack_june={h.get('pack_june_day')}  "
                f"b_only={h.get('b_only_day')}  non_b={h.get('non_b_day')}"
            )
    lines.append("")
    lines.append(f"JSON: {payload['out_json']}")
    lines.append("=" * 60)
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description="MA10 近窗风格切换打分")
    ap.add_argument("--window", type=int, default=15, help="近窗选股日数量，默认15")
    ap.add_argument("--asof", type=str, default=None, help="评价截止选股日 YYYY-MM-DD，默认用数据末日")
    ap.add_argument("--edge", type=float, default=LEAD_EDGE, help="卫星领先阈值(百分点)，默认0.35")
    ap.add_argument("--files", nargs="*", default=None, help="指定按票xlsx；默认自动发现收盘上MA10")
    args = ap.parse_args(argv)

    files = [Path(x) for x in args.files] if args.files else discover_files()
    print("加载:", *[p.name for p in files], sep="\n  ")
    df = add_flags(load_pool(files))
    sels = sorted(df["sel"].unique())
    if len(sels) < args.window:
        raise SystemExit(f"选股日不足 window={args.window}，当前仅 {len(sels)} 天")

    asof = parse_asof(args.asof) or sels[-1]
    # use sels <= asof
    sels_use = [d for d in sels if d <= asof]
    if len(sels_use) < args.window:
        raise SystemExit(f"asof={asof} 之前不足 {args.window} 个选股日（仅{len(sels_use)}）")
    win = sels_use[-args.window :]
    sc = score_window(df, win)
    dec = decide(sc, edge=float(args.edge))

    hist = []
    sels_h = sorted(df[df["sel"] <= asof]["sel"].unique())
    for i in range(args.window - 1, len(sels_h)):
        wsel = sels_h[i - args.window + 1 : i + 1]
        sc_i = score_window(df, wsel)
        dec_i = decide(sc_i, edge=float(args.edge))
        hist.append(
            {
                "asof": str(sels_h[i]),
                "decision": dec_i["decision"],
                "july_day": sc_i["july_sat"]["day_mean"],
                "june_day": sc_i["june_sat"]["day_mean"],
                "core_day": sc_i["core_ok"]["day_mean"],
                "pack_july_day": sc_i["pack_july"]["day_mean"],
                "pack_june_day": sc_i["pack_june"]["day_mean"],
                "b_only_day": sc_i["b_only"]["day_mean"],
                "non_b_day": sc_i["non_b"]["day_mean"],
                "july_n": sc_i["july_sat"]["n"],
                "june_n": sc_i["june_sat"]["n"],
                "core_n": sc_i["core_ok"]["n"],
                "b_only_n": sc_i["b_only"]["n"],
                "non_b_n": sc_i["non_b"]["n"],
            }
        )
    hint = decay_hint(sc, edge=float(args.edge))
    payload = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": [p.name for p in files],
        "data_from": str(sels[0]),
        "data_to": str(sels[-1]),
        "n_sel_days": len(sels),
        "asof": str(asof),
        "window": args.window,
        "window_from": str(win[0]),
        "window_to": str(win[-1]),
        "edge": args.edge,
        "scores": sc,
        "decision": dec["decision"],
        "rule_ui": RULE_UI.get(dec["decision"], ""),
        "reason": dec["reason"],
        "rules": dec["rules"],
        "decay_hint": hint,
        "history_tail": hist[-8:],
        "out_json": str(OUT_JSON),
    }
    text = format_report(payload)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    OUT_TXT.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
