# -*- coding: utf-8 -*-
"""生成马总逻辑1 QMT 模型交易细回测脚本（3b）

默认同时原子写入 userdata/mz1_qmt_fine_watch/current.py 供探针热加载。

示例：
  python tools/generate_mz1_qmt_fine_backtest.py --codes 000001,600000 --start 2026-09-28 --end 2026-10-26
  python tools/generate_mz1_qmt_fine_backtest.py --selection-file 选股.xlsx --from-t1
  python tools/generate_mz1_qmt_fine_backtest.py --ingest "D:\\国金证券QMT交易端2\\userdata\\log\\XtClient_FormulaOutput_20261026.log"
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategy_generator_app.backtest.mz1_qmt_fine import (
    RESULT_PREFIX,
    build_qmt_strategy_source,
    clamp_range,
    clamp_selection_days,
    default_out_dir,
    default_watch_path,
    last_watch_path,
    last_watch_write_error,
    load_mz1_strategy_params,
    parse_formula_output_file,
    parse_ymd,
    tick_window_bounds,
    trades_to_backtest_rows,
    write_qmt_strategy_file,
)


def _parse_codes(s: str):
    out = []
    for part in (s or "").replace(";", ",").replace(" ", ",").split(","):
        p = part.strip()
        if p:
            out.append(p)
    return out


def _load_selection_file(path: Path):
    try:
        # avoid importing heavy GUI; try helper modules first
        from strategy_generator_app.main import (  # type: ignore
            group_codes_and_clip_strength_by_selection_date,
        )
    except Exception:
        group_codes_and_clip_strength_by_selection_date = None
    if group_codes_and_clip_strength_by_selection_date is not None:
        try:
            by_day, _strength, hint = group_codes_and_clip_strength_by_selection_date(
                str(path)
            )
            codes = []
            sel = {}
            for d, lst in (by_day or {}).items():
                ds = str(d)[:10]
                for c in lst or []:
                    c6 = str(c).strip().replace(".", "")[:6].zfill(6)
                    codes.append(c6)
                    sel.setdefault(c6, ds)
            return codes, sel, hint or ""
        except Exception as e:
            print("[warn] group_codes via main failed:", e)

    import pandas as pd

    if path.suffix.lower() in (".xlsx", ".xls"):
        df = pd.read_excel(path)
    else:
        df = pd.read_csv(path)
    code_col = None
    date_col = None
    for name in df.columns:
        n = str(name)
        nl = n.lower()
        if code_col is None and ("代码" in n or nl in ("code", "stock_code", "证券代码")):
            code_col = name
        if date_col is None and ("选股日" in n or nl in ("selection_date", "date")):
            date_col = name
    if code_col is None:
        raise SystemExit("选股文件找不到代码列")
    codes = []
    sel = {}
    for _, row in df.iterrows():
        c6 = str(row[code_col]).strip()
        if "." in c6:
            c6 = c6.split(".", 1)[0]
        c6 = "".join(ch for ch in c6 if ch.isdigit())[-6:].zfill(6)
        if len(c6) != 6:
            continue
        codes.append(c6)
        if date_col is not None:
            ds = str(row[date_col])[:10]
            sel.setdefault(c6, ds)
    return codes, sel, "fallback parser rows=%d" % len(codes)


def cmd_generate(args: argparse.Namespace) -> int:
    sp = load_mz1_strategy_params(ROOT)
    entry_w = int(args.entry_window or sp.get("entry_window_trading_days") or 10)
    amount = float(args.amount or sp.get("buy_amount_per_stock") or 50000)
    min_order = float(sp.get("min_order_amount") or 5000)
    rise = float(sp.get("rise_percent") or 0)
    from_t1 = not args.no_from_t1

    codes = _parse_codes(args.codes or "")
    sel_map = {}
    hint = ""
    sel_days = []
    if args.selection_file:
        codes2, sel_map, hint = _load_selection_file(Path(args.selection_file))
        if not codes:
            codes = codes2
        for v in sel_map.values():
            d = parse_ymd(v)
            if d:
                sel_days.append(d)
        print("[sel]", hint)

    if not codes:
        raise SystemExit("请提供 --codes 或 --selection-file")

    w0, w1 = tick_window_bounds()
    if args.start and args.end:
        cr = clamp_range(parse_ymd(args.start), parse_ymd(args.end))
    elif sel_days:
        cr = clamp_selection_days(sel_days, entry_window=entry_w, from_t1=from_t1)
    else:
        cr = clamp_range(w0, w1)

    if cr.reason:
        print("[clamp]", cr.reason)
    if cr.dropped_selection_days:
        print(
            "[clamp] dropped selection days:",
            ",".join(cr.dropped_selection_days[:20]),
            ("..." if len(cr.dropped_selection_days) > 20 else ""),
        )

    if cr.dropped_selection_days:
        drop = set(cr.dropped_selection_days)
        sel_map = {k: v for k, v in sel_map.items() if str(v)[:10] not in drop}
        if sel_map:
            keep_codes = set(sel_map.keys())
            codes = [
                c
                for c in codes
                if (c.zfill(6) if str(c).isdigit() else str(c)) in keep_codes
            ]

    src = build_qmt_strategy_source(
        codes=codes,
        selection_date_by_code=sel_map,
        start=cr.start,
        end=cr.end,
        entry_window=entry_w,
        buy_amount_per_stock=amount,
        min_order_amount=min_order,
        rise_percent=rise,
        from_t1=from_t1,
    )
    also_watch = not bool(getattr(args, "no_watch", False))
    out = write_qmt_strategy_file(
        src,
        out_path=Path(args.out) if args.out else None,
        root=ROOT,
        also_watch=also_watch,
    )
    wp = last_watch_path()
    werr = last_watch_write_error()
    meta = {
        "out": str(out),
        "gbk": str(out.with_suffix(".gbk.py")),
        "watch": str(wp) if wp else str(default_watch_path()),
        "watch_ok": bool(wp) and werr is None,
        "watch_error": str(werr) if werr else "",
        "start": cr.start.isoformat(),
        "end": cr.end.isoformat(),
        "n_codes": len(set(codes)),
        "entry_window": entry_w,
        "result_prefix": RESULT_PREFIX,
        "clamp": cr.reason,
        "dropped_selection_days": cr.dropped_selection_days,
    }
    meta_path = out.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    if wp and werr is None:
        print("已投递 watch：%s （探针在跑会自动重载）" % wp)
    elif werr:
        print("[warn] watch 写入失败：%s" % werr)
    print("备份：%s ；首次请用模型交易加载 python\\探针.py 并启动" % out)
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    path = Path(args.ingest)
    result = parse_formula_output_file(path)
    rows = trades_to_backtest_rows(result.get("trades") or [])
    out_dir = default_out_dir(ROOT)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_json = Path(args.out) if args.out else (out_dir / ("ingest_%s.json" % stamp))
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    out_csv = out_json.with_suffix(".trades.csv")
    if rows:
        with out_csv.open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
    print(
        json.dumps(
            {
                "source": str(path),
                "trade_count": result.get("trade_count"),
                "summary": result.get("summary"),
                "errors": result.get("errors"),
                "out_json": str(out_json),
                "out_csv": str(out_csv) if rows else "",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def main():
    ap = argparse.ArgumentParser(description="马总逻辑1 QMT 细回测 3b")
    ap.add_argument("--codes", default="", help="逗号分隔代码")
    ap.add_argument("--selection-file", default="", help="选股文件 Excel/CSV")
    ap.add_argument("--start", default="")
    ap.add_argument("--end", default="")
    ap.add_argument("--entry-window", type=int, default=0)
    ap.add_argument("--amount", type=float, default=0)
    ap.add_argument("--no-from-t1", action="store_true")
    ap.add_argument("--out", default="", help="输出策略 py 或 ingest json")
    ap.add_argument("--no-watch", action="store_true", help="不写 userdata watch current.py")
    ap.add_argument("--ingest", default="", help="解析 FormulaOutput 日志")
    args = ap.parse_args()
    if args.ingest:
        return cmd_ingest(args)
    return cmd_generate(args)


if __name__ == "__main__":
    raise SystemExit(main())
