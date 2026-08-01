#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
载入批量回测 JSON（买入端导出），用「新卖出」等策略做下一轮接续回测，
扫描弹性卖出 tp10 参数并输出对比 CSV。

默认与 UI「下一轮批量回测」一致：
  - 起算 T+1（选股次日为卖出日）
  - 持有 1 个交易日（今天买、次日卖；窗口仅卖出日一天）
  - 定时清仓 14:56（末日出清日=窗口内第 1 天）
  - 未卖出持仓按当日收盘价计入权益（引擎 equity_curve / last_prices）
"""

from __future__ import annotations

import argparse
import configparser
import csv
import json
import os
import re
import sys
import time
from copy import deepcopy
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from strategy_generator_app.config.strategy_config import (  # noqa: E402
    load_strategy_by_id,
    strategy_uses_scheduled_clear,
    strip_scheduled_clear_params,
)
from strategy_generator_app.trading_calendar import (  # noqa: E402
    backtest_window_from_selection_day,
    next_trading_day_after,
    trading_day_window_from_start,
)


def _parse_iso_date(val: Any) -> Optional[date]:
    if not val:
        return None
    s = str(val).strip()[:10]
    try:
        return date.fromisoformat(s)
    except ValueError:
        return None


def normalize_batch_bundle(data: dict) -> dict:
    fixed = {}
    for k, v in data.items():
        nk = k.lstrip("\ufeff") if isinstance(k, str) else k
        fixed[nk] = v
    if int(float(fixed.get("version", 0))) != 2:
        raise ValueError("需要 version=2 的 batch_backtest JSON")
    if str(fixed.get("kind") or "").strip().lower() != "batch_backtest":
        raise ValueError("需要 kind=batch_backtest")
    segs = fixed.get("segments")
    if not isinstance(segs, list) or not segs:
        raise ValueError("segments 为空")
    return fixed


def codes_from_segment(seg: dict) -> List[str]:
    out: List[str] = []
    for k, p in (seg.get("positions") or {}).items():
        try:
            vol = int((p or {}).get("volume") or 0)
        except (TypeError, ValueError):
            vol = 0
        if vol <= 0:
            continue
        code_6 = str(k).strip().replace(".", "")[:6].zfill(6)
        if len(code_6) == 6:
            out.append(code_6)
    return out


def initial_positions_from_segment(seg: dict) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for k, p in (seg.get("positions") or {}).items():
        code_6 = str(k).strip().replace(".", "")[:6].zfill(6)
        if len(code_6) != 6:
            continue
        try:
            vol = int((p or {}).get("volume") or 0)
            cost = float((p or {}).get("cost") or 0)
        except (TypeError, ValueError):
            continue
        if vol > 0:
            out[code_6] = {"volume": vol, "cost": cost}
    return out


def compute_chained_window(
    seg: dict, *, from_t1: bool, hold_n: int
) -> Tuple[Optional[date], Optional[date], str]:
    sel = _parse_iso_date(seg.get("batch_selection_date"))
    br = seg.get("backtest_range") or {}
    prior_start = _parse_iso_date(br.get("start"))
    prior_end = _parse_iso_date(br.get("end")) or _parse_iso_date(br.get("last_equity_date"))
    if not sel or prior_start is None or prior_end is None:
        return None, None, "缺少选股日或上轮区间"
    s_req, e_req, msg = backtest_window_from_selection_day(
        sel, start_next_trading_day=from_t1, hold_trading_days=hold_n
    )
    if s_req is None:
        return None, None, msg
    if s_req <= prior_end:
        n_after = next_trading_day_after(prior_start)
        if n_after is None:
            return None, None, "无法计算上轮 start 次日"
        ns = s_req if s_req > n_after else n_after
        s_eff, e_eff, w2 = trading_day_window_from_start(ns, hold_n)
        if s_eff is None:
            return None, None, w2 or "区间不足"
        note = f"重叠顺延→{s_eff}"
        return s_eff, e_eff, note
    return s_req, e_req, ""


def set_dynamic_thresholds(value: int) -> None:
    ini_path = os.path.join(ROOT, "data", "config.ini")
    cfg = configparser.ConfigParser()
    cfg.read(ini_path, encoding="utf-8-sig")
    if not cfg.has_section("Elastic"):
        cfg.add_section("Elastic")
    cfg.set("Elastic", "dynamic_thresholds", str(int(value)))
    with open(ini_path, "w", encoding="utf-8") as f:
        cfg.write(f)
    import core.elastic_sell as es

    es._ELASTIC_GLOBAL_CACHE = None  # type: ignore[attr-defined]
    try:
        import strategy_generator_app.backtest.simulator as sim

        sim._ELASTIC_CFG_CACHE = None  # type: ignore[attr-defined]
    except Exception:
        pass


def merge_clear_time(params: dict, clear_time: str) -> dict:
    p = dict(params or {})
    if strategy_uses_scheduled_clear("", p, "卖"):
        p["scheduled_clear_time"] = clear_time
    else:
        strip_scheduled_clear_params(p)
    return p


def segment_initial_equity(cash: float, init_pos: dict) -> float:
    eq = float(cash or 0)
    for pos in init_pos.values():
        eq += int(pos.get("volume") or 0) * float(pos.get("cost") or 0)
    return eq


def prepare_strategy_code_for_tp10_overrides(code: str, param_overrides: dict) -> str:
    """策略代码内 _tp10_ov=dict(...) 会覆盖 JSON；扫描 tp10 参数时强制走 params。"""
    tp10_keys = {
        "tp10_ratio_low",
        "tp10_up_low",
        "tp10_up_high",
        "tp10_drop_low",
        "tp10_drop_high",
        "tp10_blend_low",
        "tp10_blend_high",
    }
    if not param_overrides or not tp10_keys.intersection(param_overrides):
        return code
    patched, n = re.subn(
        r"(?m)^(\s*)_tp10_ov\s*=\s*dict\([^)]*\)\s*$",
        r"\1_tp10_ov = None  # sweep: tp10 from strategy_params",
        code,
        count=1,
    )
    if n == 0:
        patched, n2 = re.subn(
            r"(?m)^(\s*)_tp10_ov\s*=\s*None\s*(#.*)?$",
            r"\1_tp10_ov = None  # sweep: tp10 from strategy_params",
            code,
            count=1,
        )
        if n2 == 0:
            return code
    return patched


def run_one_combo(
    *,
    combo_id: str,
    combo_label: str,
    segments: List[dict],
    strategy_code: str,
    base_params: dict,
    param_overrides: dict,
    dynamic_thresholds: int,
    from_t1: bool,
    hold_n: int,
    clear_time: str,
    max_segments: Optional[int],
    get_name,
) -> dict:
    from strategy_generator_app.backtest import run_backtest, compute_metrics

    set_dynamic_thresholds(dynamic_thresholds)
    run_params = merge_clear_time(deepcopy(base_params), clear_time)
    run_params.update(param_overrides)
    strategy_code = prepare_strategy_code_for_tp10_overrides(strategy_code, param_overrides)

    seg_returns: List[float] = []
    seg_weights: List[float] = []
    total_trades = 0
    ran = 0
    skipped = 0
    t0 = time.time()

    for i, seg in enumerate(segments):
        if max_segments is not None and i >= max_segments:
            break
        sel_s = seg.get("batch_selection_date", "")
        start_d, end_d, note = compute_chained_window(seg, from_t1=from_t1, hold_n=hold_n)
        icash = float(seg.get("final_cash") or 0)
        init_pos = initial_positions_from_segment(seg)
        codes = codes_from_segment(seg)
        if start_d is None or (not codes and icash <= 0):
            skipped += 1
            continue
        try:
            result = run_backtest(
                strategy_code=strategy_code,
                strategy_params=run_params,
                stock_codes_6=codes,
                start_date=start_d,
                end_date=end_d,
                initial_cash=icash,
                get_stock_name=get_name,
                use_engine_form=False,
                use_tick_level=True,
                strategy_generation_time="09:25",
                strategy_run_start_time="09:30",
                strategy_run_end_time="15:00",
                initial_positions=init_pos or None,
            )
            init_eq = segment_initial_equity(icash, init_pos)
            metrics = compute_metrics(
                result.get("equity_curve") or [],
                result.get("trades") or [],
                icash,
                result.get("final_positions"),
                result.get("last_prices"),
                initial_positions=init_pos or None,
                buy_and_hold_total=result.get("buy_and_hold_total"),
            )
            tr = float(metrics.get("total_return") or 0) * 100.0
            tc = int(metrics.get("trade_count") or 0)
            seg_returns.append(tr)
            seg_weights.append(init_eq)
            total_trades += tc
            ran += 1
            print(
                f"  [{combo_id}] {i+1}/{len(segments)} {sel_s} "
                f"{start_d}~{end_d} ret={tr:.3f}% trades={tc} {note}",
                flush=True,
            )
        except Exception as ex:
            skipped += 1
            print(f"  [{combo_id}] skip {sel_s}: {ex}", flush=True)

    if seg_returns:
        mean_ret = sum(seg_returns) / len(seg_returns)
        w_sum = sum(seg_weights) or 1.0
        wmean_ret = sum(r * w for r, w in zip(seg_returns, seg_weights)) / w_sum
        med_ret = sorted(seg_returns)[len(seg_returns) // 2]
    else:
        mean_ret = wmean_ret = med_ret = 0.0

    elapsed = time.time() - t0
    return {
        "combo_id": combo_id,
        "label": combo_label,
        "dynamic_thresholds": dynamic_thresholds,
        **param_overrides,
        "segments_ran": ran,
        "segments_skipped": skipped,
        "mean_return_pct": round(mean_ret, 4),
        "weighted_mean_return_pct": round(wmean_ret, 4),
        "median_return_pct": round(med_ret, 4),
        "total_trades": total_trades,
        "elapsed_s": round(elapsed, 1),
    }


def build_param_grid(mode: str) -> List[Tuple[str, str, dict, int]]:
    """返回 (combo_id, label, param_overrides, dynamic_thresholds)"""
    base = {}
    rows: List[Tuple[str, str, dict, int]] = []

    def add(cid: str, label: str, ov: dict, dt: int = 2):
        p = {**base, **ov}
        rows.append((cid, label, p, dt))

    if mode == "quick":
        add("dt1_base", "dt=1 基准(2.5/5, blend3/1.5, 20%)", {}, 1)
        add("dt2_base", "dt=2 基准(2.5/5, blend3/1.5, 20%)", {}, 2)
        add("ratio25", "dt=2 ratio_low=0.25", {"tp10_ratio_low": 0.25})
        add("ratio30", "dt=2 ratio_low=0.30", {"tp10_ratio_low": 0.30})
        return rows

    if mode == "ratio":
        add("dt2_base", "dt=2 基准 20/80", {})
        for r in (0.10, 0.15, 0.20, 0.25, 0.30, 0.35):
            add(f"ratio_{int(r*100)}", f"dt=2 ratio_low={r}", {"tp10_ratio_low": r})
        return rows

    if mode == "blend":
        add("dt2_base", "dt=2 blend 3.0/1.5", {})
        for bl, bh in ((2.5, 1.5), (3.0, 2.0), (3.5, 1.5), (3.0, 1.0)):
            add(f"blend_{bl}_{bh}", f"blend {bl}/{bh}", {"tp10_blend_low": bl, "tp10_blend_high": bh})
        return rows

    if mode == "drop":
        add("dt2_base", "dt=2 drop 2.5/5", {})
        for dl, dh in ((2.0, 4.0), (2.5, 5.0), (3.0, 5.0), (3.0, 6.0)):
            add(f"drop_{dl}_{dh}", f"drop {dl}/{dh}", {"tp10_drop_low": dl, "tp10_drop_high": dh})
        return rows

    if mode == "trigger":
        add("dt2_base", "up 5/7.5%", {})
        for uh in (0.065, 0.070, 0.075, 0.080, 0.085):
            add(f"uphigh_{uh}", f"up_high={uh}", {"tp10_up_high": uh})
        return rows

    if mode == "blend_high_up6":
        # 固定高档触发 +6%，扫描 tp10_blend_high（低档 blend 保持策略默认 3.0）
        base_ov = {"tp10_up_high": 0.06, "tp10_ratio_low": 0.25}
        for bh in (1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0):
            add(
                f"bh_{str(bh).replace('.', '_')}",
                f"up_high=6% blend_high={bh}",
                {**base_ov, "tp10_blend_high": bh},
            )
        return rows

    if mode in ("blend_high_up65", "blend_high_up7", "blend_high_up65_7"):
        up_specs = []
        if mode == "blend_high_up65":
            up_specs = [(0.065, "65", "6.5%")]
        elif mode == "blend_high_up7":
            up_specs = [(0.07, "70", "7%")]
        else:
            up_specs = [(0.065, "65", "6.5%"), (0.07, "70", "7%")]
        for uh, tag, lbl in up_specs:
            base_ov = {"tp10_up_high": uh, "tp10_ratio_low": 0.25}
            for bh in (1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0):
                add(
                    f"up{tag}_bh_{str(bh).replace('.', '_')}",
                    f"up_high={lbl} blend_high={bh}",
                    {**base_ov, "tp10_blend_high": bh},
                )
        return rows

    if mode == "path_a":
        # 单点卖出「未早封涨停」激活时刻；其余 tp10 走策略代码/JSON 默认
        for t in ("09:30:00", "09:40:00", "09:50:00", "10:00:00", "10:30:00", "11:00:00"):
            cid = "path_a_" + t.replace(":", "")
            add(cid, f"path_a_activate_at={t[:5]}", {"path_a_activate_at": t})
        return rows

    # full = 核心对比 + 单因素扫描（组合较多，耗时长）
    add("dt1_base", "dt=1 基准", {}, 1)
    add("dt2_base", "dt=2 基准", {}, 2)
    for r in (0.15, 0.20, 0.25, 0.30):
        if r != 0.25:
            add(f"ratio_{int(r*100)}", f"ratio {int(r*100)}%", {"tp10_ratio_low": r})
    for bl, bh in ((2.5, 1.5), (3.0, 2.0)):
        if (bl, bh) != (3.0, 1.5):
            add(f"blend_{bl}_{bh}", f"blend {bl}/{bh}", {"tp10_blend_low": bl, "tp10_blend_high": bh})
    for dl, dh in ((2.0, 4.0), (3.0, 5.0)):
        if (dl, dh) != (2.5, 5.0):
            add(f"drop_{dl}_{dh}", f"drop {dl}/{dh}", {"tp10_drop_low": dl, "tp10_drop_high": dh})
    for uh in (0.070, 0.080):
        if uh != 0.075:
            add(f"up_{uh}", f"up_high {uh}", {"tp10_up_high": uh})
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="弹性卖出参数扫描（载入批量买入 JSON → 卖出接续回测）")
    ap.add_argument(
        "--batch-json",
        default=os.path.join(ROOT, "history_data", "新回测", "batch_backtest_export_买：突破5日线-真突破_3-5月.json"),
        help="version=2 批量回测导出文件",
    )
    ap.add_argument("--sell-strategy-id", default="strategy_e9c83928", help="卖出策略 ID（默认新卖出）")
    ap.add_argument("--hold-days", type=int, default=1, help="持有交易日数（默认1=仅卖出日一天）")
    ap.add_argument("--from-t1", action="store_true", default=True, help="T+1 起算（默认开）")
    ap.add_argument("--clear-time", default="14:56:00")
    ap.add_argument(
        "--grid",
        default="quick",
        choices=[
            "quick",
            "full",
            "ratio",
            "blend",
            "drop",
            "trigger",
            "blend_high_up6",
            "blend_high_up65",
            "blend_high_up7",
            "blend_high_up65_7",
            "path_a",
        ],
    )
    ap.add_argument("--max-segments", type=int, default=None, help="仅跑前 N 档（调试）")
    ap.add_argument(
        "--out",
        default=None,
        help="输出 CSV 路径（默认 history_data/新回测/sweep_elastic_<ts>.csv）",
    )
    args = ap.parse_args()

    if not os.path.isfile(args.batch_json):
        # 尝试模糊匹配
        import glob

        cands = glob.glob(os.path.join(ROOT, "history_data", "**", "*真突破*3-5*.json"), recursive=True)
        if cands:
            args.batch_json = cands[-1]
            print("使用 JSON:", args.batch_json)
        else:
            raise SystemExit(f"找不到文件: {args.batch_json}")

    with open(args.batch_json, encoding="utf-8-sig") as f:
        bundle = normalize_batch_bundle(json.load(f))
    segments = bundle["segments"]
    print(f"载入 {len(segments)} 档 | 来源: {bundle.get('source_strategy_name')}")

    cfg = load_strategy_by_id(args.sell_strategy_id)
    if not cfg:
        raise SystemExit(f"未找到策略 {args.sell_strategy_id}")
    code = cfg.strategy_code or ""
    base_params = dict(cfg.strategy_params or {})
    print(f"卖出策略: {cfg.name} ({args.sell_strategy_id})")

    try:
        from strategy_generator_app.backtest.stock_info_loader import get_stock_name_callable

        get_name = get_stock_name_callable()
    except Exception:
        get_name = lambda c: ""  # noqa: E731

    grid = build_param_grid(args.grid)
    print(f"参数组合数: {len(grid)} | hold={args.hold_days}天 | clear={args.clear_time} | grid={args.grid}")

    results: List[dict] = []
    orig_dt = None
    try:
        import core.elastic_sell as es

        orig_cfg = es.load_elastic_global_config(force_reload=True)
        orig_dt = orig_cfg.dynamic_thresholds
    except Exception:
        pass

    for cid, label, overrides, dt in grid:
        print(f"\n=== {cid}: {label} (dynamic_thresholds={dt}) ===")
        row = run_one_combo(
            combo_id=cid,
            combo_label=label,
            segments=segments,
            strategy_code=code,
            base_params=base_params,
            param_overrides=overrides,
            dynamic_thresholds=dt,
            from_t1=args.from_t1,
            hold_n=args.hold_days,
            clear_time=args.clear_time,
            max_segments=args.max_segments,
            get_name=get_name,
        )
        row["label"] = label
        results.append(row)

    if orig_dt is not None:
        set_dynamic_thresholds(orig_dt)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = args.out or os.path.join(
        ROOT, "history_data", "新回测", f"sweep_elastic_{args.grid}_{ts}.csv"
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    fieldnames: List[str] = []
    seen = set()
    for r in results:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                fieldnames.append(k)
    with open(out_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(results)

    print("\n" + "=" * 60)
    print(f"已写入: {out_path}\n")
    print(f"{'combo':<16} {'mean%':>8} {'wmean%':>8} {'med%':>8} {'trades':>7} {'ran':>4}")
    for r in sorted(results, key=lambda x: -x["mean_return_pct"]):
        print(
            f"{r['combo_id']:<16} {r['mean_return_pct']:>8.3f} "
            f"{r['weighted_mean_return_pct']:>8.3f} {r['median_return_pct']:>8.3f} "
            f"{r['total_trades']:>7} {r['segments_ran']:>4}"
        )


if __name__ == "__main__":
    main()
