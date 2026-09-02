# -*- coding: utf-8 -*-
"""
验证 ma10_regime_switch 打分是否有样本外价值（用已有 6+7 月上MA10 数据）。

方法（滚动 walk-forward）:
  1) 用截至 asof 的近 `window` 个交易开始日打分 → 得到 decision
  2) 用 asof 之后的 `fwd` 个交易开始日，比较各袖套真实收益（样本外）
  3) 看「按脚本决策执行」是否优于：永远 CORE / 永远七月包 / 永远六月包 / 全样本

用法:
  python tools/validate_ma10_regime_switch.py
  python tools/validate_ma10_regime_switch.py --window 10 --fwd 5
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import ma10_regime_switch as rs  # noqa: E402

OUT_JSON = rs.DIR / "_ma10_regime_switch_validation.json"
OUT_TXT = rs.DIR / "_ma10_regime_switch_validation.txt"

PACK_COL = {
    "CORE_ONLY": "core_ok",
    "CORE_PLUS_JULY": "pack_july",
    "CORE_PLUS_JUNE": "pack_june",
}


def day_mean(sub: pd.DataFrame) -> float | None:
    if sub is None or sub.empty:
        return None
    return float(sub.groupby("start")["ret"].mean().mean())


def ticket_mean(sub: pd.DataFrame) -> float | None:
    if sub is None or sub.empty:
        return None
    return float(sub["ret"].mean())


def sleeve(df: pd.DataFrame, col: str) -> pd.DataFrame:
    return df[df[col].fillna(False)]


def summarize(vals: list[float]) -> dict:
    arr = np.array([v for v in vals if v is not None], dtype=float)
    if len(arr) == 0:
        return {"n": 0, "mean": None, "med": None, "pos_rate": None}
    return {
        "n": int(len(arr)),
        "mean": round(float(arr.mean()), 4),
        "med": round(float(np.median(arr)), 4),
        "pos_rate": round(float((arr > 0).mean() * 100), 2),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Walk-forward validate MA10 regime switch")
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--fwd", type=int, default=5, help="样本外前瞻交易开始日数")
    ap.add_argument("--edge", type=float, default=rs.LEAD_EDGE)
    args = ap.parse_args(argv)

    files = rs.discover_files()
    df = rs.add_flags(rs.load_pool(files))
    df = rs.ensure_start(df)
    sels = sorted(x for x in df["start"].dropna().unique())
    print("数据", sels[0], "→", sels[-1], "天", len(sels))
    print("window", args.window, "fwd", args.fwd, "edge", args.edge)

    rows = []
    # need window lookback + at least 1 forward day
    for i in range(args.window - 1, len(sels) - 1):
        look = sels[i - args.window + 1 : i + 1]
        asof = sels[i]
        fwd_sels = sels[i + 1 : i + 1 + args.fwd]
        if not fwd_sels:
            continue

        sc = rs.score_window(df, look, asof=asof)
        dec = rs.decide(sc, edge=float(args.edge))
        decision = dec["decision"]

        oos = df[df["start"].isin(fwd_sels)].copy()
        metrics = {}
        for name, col in [
            ("baseline", None),
            ("core_ok", "core_ok"),
            ("pack_july", "pack_july"),
            ("pack_june", "pack_june"),
            ("july_sat", "july_sat"),
            ("june_sat", "june_sat"),
        ]:
            sub = oos if col is None else sleeve(oos, col)
            metrics[name] = {
                "n": int(len(sub)),
                "day_mean": None if sub.empty else round(day_mean(sub), 4),
                "mean": None if sub.empty else round(ticket_mean(sub), 4),
            }

        chosen_col = PACK_COL[decision]
        chosen = metrics[chosen_col]
        # oracle among three packs
        pack_days = {
            "CORE_ONLY": metrics["core_ok"]["day_mean"],
            "CORE_PLUS_JULY": metrics["pack_july"]["day_mean"],
            "CORE_PLUS_JUNE": metrics["pack_june"]["day_mean"],
        }
        valid_packs = {k: v for k, v in pack_days.items() if v is not None}
        oracle = max(valid_packs, key=valid_packs.get) if valid_packs else None
        oracle_day = valid_packs.get(oracle) if oracle else None

        # direction: IS leading satellite vs OOS which sat pack won
        is_july = sc["july_sat"]["day_mean"]
        is_june = sc["june_sat"]["day_mean"]
        oos_july = metrics["july_sat"]["day_mean"]
        oos_june = metrics["june_sat"]["day_mean"]
        sat_dir_hit = None
        if is_july is not None and is_june is not None and oos_july is not None and oos_june is not None:
            is_pref_july = is_july >= is_june
            oos_pref_july = oos_july >= oos_june
            sat_dir_hit = bool(is_pref_july == oos_pref_july)

        beat_core = None
        if chosen["day_mean"] is not None and metrics["core_ok"]["day_mean"] is not None:
            beat_core = chosen["day_mean"] - metrics["core_ok"]["day_mean"]
        beat_baseline = None
        if chosen["day_mean"] is not None and metrics["baseline"]["day_mean"] is not None:
            beat_baseline = chosen["day_mean"] - metrics["baseline"]["day_mean"]
        regret = None
        if chosen["day_mean"] is not None and oracle_day is not None:
            regret = oracle_day - chosen["day_mean"]

        rows.append(
            {
                "asof": str(asof),
                "fwd_from": str(fwd_sels[0]),
                "fwd_to": str(fwd_sels[-1]),
                "fwd_n_days": len(fwd_sels),
                "decision": decision,
                "oracle": oracle,
                "oracle_hit": decision == oracle,
                "sat_dir_hit": sat_dir_hit,
                "is_july_day": is_july,
                "is_june_day": is_june,
                "oos_baseline_day": metrics["baseline"]["day_mean"],
                "oos_core_day": metrics["core_ok"]["day_mean"],
                "oos_pack_july_day": metrics["pack_july"]["day_mean"],
                "oos_pack_june_day": metrics["pack_june"]["day_mean"],
                "oos_chosen_day": chosen["day_mean"],
                "oos_chosen_mean": chosen["mean"],
                "oos_chosen_n": chosen["n"],
                "beat_core": None if beat_core is None else round(beat_core, 4),
                "beat_baseline": None if beat_baseline is None else round(beat_baseline, 4),
                "regret_vs_oracle": None if regret is None else round(regret, 4),
            }
        )

    if not rows:
        raise SystemExit("没有可验证的滚动点（数据太短？）")

    # strategy path: average OOS day_mean of chosen
    chosen_days = [r["oos_chosen_day"] for r in rows]
    core_days = [r["oos_core_day"] for r in rows]
    july_days = [r["oos_pack_july_day"] for r in rows]
    june_days = [r["oos_pack_june_day"] for r in rows]
    base_days = [r["oos_baseline_day"] for r in rows]

    # always-X path uses that pack's OOS each step
    summary = {
        "window": args.window,
        "fwd": args.fwd,
        "edge": args.edge,
        "n_steps": len(rows),
        "asof_from": rows[0]["asof"],
        "asof_to": rows[-1]["asof"],
        "files": [p.name for p in files],
        "oos_day_mean": {
            "script_chosen": summarize(chosen_days),
            "always_core": summarize(core_days),
            "always_pack_july": summarize(july_days),
            "always_pack_june": summarize(june_days),
            "always_baseline": summarize(base_days),
        },
        "oracle_hit_rate": round(
            100.0 * sum(1 for r in rows if r["oracle_hit"]) / len(rows), 2
        ),
        "sat_dir_hit_rate": round(
            100.0
            * sum(1 for r in rows if r["sat_dir_hit"] is True)
            / max(1, sum(1 for r in rows if r["sat_dir_hit"] is not None)),
            2,
        ),
        "sat_dir_n": sum(1 for r in rows if r["sat_dir_hit"] is not None),
        "decision_counts": pd.Series([r["decision"] for r in rows]).value_counts().to_dict(),
        "avg_beat_core": summarize([r["beat_core"] for r in rows if r["beat_core"] is not None]),
        "avg_beat_baseline": summarize(
            [r["beat_baseline"] for r in rows if r["beat_baseline"] is not None]
        ),
        "avg_regret": summarize(
            [r["regret_vs_oracle"] for r in rows if r["regret_vs_oracle"] is not None]
        ),
        "steps": rows,
    }

    # text report
    lines = []
    lines.append("=" * 64)
    lines.append("MA10 风格切换 · 滚动样本外验证")
    lines.append("=" * 64)
    lines.append(
        f"lookback={args.window}  fwd={args.fwd}  steps={len(rows)}  "
        f"asof {rows[0]['asof']} → {rows[-1]['asof']}"
    )
    lines.append("")
    lines.append("【1】样本外日均等权%（各步平均）—— 越高越好")
    for k, lab in [
        ("script_chosen", "按脚本决策执行"),
        ("always_core", "永远只做 CORE"),
        ("always_pack_july", "永远 CORE∪七月"),
        ("always_pack_june", "永远 CORE∪六月"),
        ("always_baseline", "永远上MA10全做"),
    ]:
        s = summary["oos_day_mean"][k]
        lines.append(
            f"  {lab:18s}  mean={s['mean']:+.4f}%  med={s['med']:+.4f}%  "
            f"正步率={s['pos_rate']}%  n={s['n']}"
        )
    lines.append("")
    lines.append("【2】方向/命中")
    lines.append(
        f"  卫星方向延续命中率(近窗谁强 → 下段谁强): "
        f"{summary['sat_dir_hit_rate']}%  (n={summary['sat_dir_n']})"
    )
    lines.append(f"  三选一 oracle 命中率: {summary['oracle_hit_rate']}%")
    lines.append(f"  决策分布: {summary['decision_counts']}")
    lines.append("")
    lines.append("【3】相对增益")
    bc = summary["avg_beat_core"]
    bb = summary["avg_beat_baseline"]
    rg = summary["avg_regret"]
    lines.append(
        f"  vs CORE: 平均 {bc['mean']:+.4f}pp  (正步率 {bc['pos_rate']}%)"
    )
    lines.append(
        f"  vs 全样本: 平均 {bb['mean']:+.4f}pp  (正步率 {bb['pos_rate']}%)"
    )
    lines.append(
        f"  vs 完美oracle 遗憾: 平均 {rg['mean']:+.4f}pp"
    )
    lines.append("")
    lines.append("【4】逐步明细（节选末 10 步）")
    lines.append(
        "asof       decision          oos_chosen  oos_core  oos_july  oos_june  oracle hit"
    )
    for r in rows[-10:]:
        lines.append(
            f"{r['asof']}  {r['decision']:16s}  "
            f"{r['oos_chosen_day']!s:>9}  {r['oos_core_day']!s:>8}  "
            f"{r['oos_pack_july_day']!s:>8}  {r['oos_pack_june_day']!s:>8}  "
            f"{r['oracle']} {r['oracle_hit']}"
        )
    lines.append("")
    # verdict
    sc_m = summary["oos_day_mean"]["script_chosen"]["mean"]
    core_m = summary["oos_day_mean"]["always_core"]["mean"]
    jul_m = summary["oos_day_mean"]["always_pack_july"]["mean"]
    jun_m = summary["oos_day_mean"]["always_pack_june"]["mean"]
    base_m = summary["oos_day_mean"]["always_baseline"]["mean"]
    best_fixed = max(
        [("CORE", core_m), ("JULY包", jul_m), ("JUNE包", jun_m), ("全样本", base_m)],
        key=lambda x: (x[1] is not None, x[1] or -999),
    )
    if sc_m is not None and best_fixed[1] is not None and sc_m >= best_fixed[1] - 1e-9:
        verdict = f"脚本路径 ≥ 最佳固定策略({best_fixed[0]} {best_fixed[1]:+.4f}%) → 近窗切换有样本外价值"
    elif sc_m is not None and core_m is not None and sc_m >= core_m and sc_m >= (base_m or -999):
        verdict = "脚本不差于 CORE/全样本，但未必打过「永远七月包」——切换有用、未必最优"
    else:
        verdict = "脚本路径弱于部分固定策略 → 近窗信号噪声大，8月宜偏保守(CORE)或加大 edge/window"
    lines.append("【结论】" + verdict)
    lines.append(f"JSON: {OUT_JSON}")
    lines.append("=" * 64)
    text = "\n".join(lines)

    summary["verdict"] = verdict
    OUT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    OUT_TXT.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
