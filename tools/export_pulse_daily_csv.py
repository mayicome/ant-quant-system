# -*- coding: utf-8 -*-
"""导出两年交易日脉冲形态 CSV（依赖个股日线宽度，默认读 data/daily_full）。

须等 daily_full 补齐后再跑；不要用残缺的 daily_cache。

用法：
  python tools/export_pulse_daily_csv.py
  python tools/export_pulse_daily_csv.py --from-date 2025-01-01 --to-date 2026-12-31
  python tools/export_pulse_daily_csv.py --cache-dir data/daily_full
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.market_breadth import DAILY_FULL, compute_breadth_panel  # noqa: E402
from utils.market_regime_rules import apply_regime_labels, load_rules  # noqa: E402

OUT_DIR = ROOT / "data" / "market_regime"
OUT_CSV = OUT_DIR / "pulse_daily.csv"

# 脉冲 CSV 精简列（计算用）；完整宽度仍可另存 market_regime_daily
PULSE_COLS = [
    "trade_date",
    "pulse",
    "sentiment",
    "divergence",
    "backdrop",
    "csi_close",
    "csi_ret_1d",
    "n_universe",
    "n_up",
    "n_down",
    "ADR",
    "UDV",
    "TRIN",
    "breadth_ok",
    "rule_version",
]


def export_pulse(
    *,
    from_date: str = "2025-01-01",
    to_date: str = "",
    cache_dir: Path = DAILY_FULL,
    out_csv: Path = OUT_CSV,
) -> Dict[str, Any]:
    rules = load_rules()
    min_bars = int(rules.get("min_bars_for_ipo_filter") or 60)
    min_universe = int(rules.get("min_universe") or 3000)

    if not cache_dir.is_dir():
        raise SystemExit("缓存目录不存在: %s" % cache_dir)
    n_csv = len(list(cache_dir.glob("*.csv")))
    print("[export] cache_dir=%s csv≈%d" % (cache_dir, n_csv))
    if n_csv < min_universe:
        raise SystemExit(
            "个股日线过少（%d < min_universe=%d），请等 daily_full 下完再跑"
            % (n_csv, min_universe)
        )

    print("[export] breadth %s .. %s" % (from_date or "(start)", to_date or "(end)"))
    panel = compute_breadth_panel(
        from_date=from_date or None,
        to_date=to_date or None,
        min_listed_trading_days=min_bars,
        min_universe=min_universe,
        cache_dir=cache_dir,
    )
    if panel.empty:
        raise SystemExit("empty panel")

    labeled = apply_regime_labels(panel, rules)
    cols = [c for c in PULSE_COLS if c in labeled.columns]
    out = labeled[cols].copy()

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_csv.with_suffix(out_csv.suffix + ".tmp")
    out.to_csv(tmp, index=False, encoding="utf-8-sig")
    try:
        tmp.replace(out_csv)
    except OSError:
        fallback = out_csv.with_name(out_csv.stem + ".new.csv")
        if fallback.exists():
            fallback.unlink()
        tmp.replace(fallback)
        out_csv = fallback
        print("[export] WARN: 目标被占用，写入", out_csv)

    ok_days = int(out["breadth_ok"].sum()) if "breadth_ok" in out.columns else 0
    pulse_counts = out["pulse"].value_counts().to_dict() if "pulse" in out.columns else {}
    meta = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "cache_dir": str(cache_dir),
        "csi_code": "000985.SH",
        "rule_version": rules.get("rule_version"),
        "rows": int(len(out)),
        "date_from": str(out["trade_date"].iloc[0]),
        "date_to": str(out["trade_date"].iloc[-1]),
        "breadth_ok_days": ok_days,
        "pulse_counts": pulse_counts,
        "out_csv": str(out_csv),
    }
    meta_path = out_csv.parent / "pulse_export_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "[export] rows=%d %s..%s breadth_ok=%d"
        % (meta["rows"], meta["date_from"], meta["date_to"], ok_days)
    )
    print("[export] pulse", pulse_counts)
    print("[export] csv", out_csv)
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description="Export pulse daily CSV from daily_full")
    ap.add_argument("--from-date", default="2025-01-01")
    ap.add_argument("--to-date", default="")
    ap.add_argument("--cache-dir", default=str(DAILY_FULL))
    ap.add_argument("--out", default=str(OUT_CSV))
    args = ap.parse_args()
    export_pulse(
        from_date=args.from_date,
        to_date=args.to_date,
        cache_dir=Path(args.cache_dir),
        out_csv=Path(args.out),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
