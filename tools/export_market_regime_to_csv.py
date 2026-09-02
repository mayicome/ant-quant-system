# -*- coding: utf-8 -*-
"""导出市场行情日度描绘 CSV（ADR/TRIN + 中证全指 000985）。

输出：data/market_regime/market_regime_daily.csv（本地分析用，不进 COS）

用法：
  python tools/export_market_regime_to_csv.py --from-date 2026-01-01
  python tools/export_market_regime_to_csv.py --incremental
  python tools/export_market_regime_to_csv.py --fetch-csi --from-date 2026-01-01
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.market_breadth import (  # noqa: E402
    DAILY_FULL,
    DEFAULT_MIN_LISTED_TD,
    DEFAULT_MIN_UNIVERSE,
    INDEX_CACHE,
    compute_breadth_panel,
)
from utils.market_regime_rules import apply_regime_labels, load_rules  # noqa: E402

OUT_DIR = ROOT / "data" / "market_regime"
OUT_CSV = OUT_DIR / "market_regime_daily.csv"
# 增量需覆盖 ma10 + 背离回看，多留交易日余量
INCREMENTAL_LOOKBACK_CALENDAR_DAYS = 45


def _merge_by_trade_date(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    if old is None or old.empty:
        return new.copy()
    if new is None or new.empty:
        return old.copy()
    old = old.copy()
    new = new.copy()
    old["trade_date"] = old["trade_date"].astype(str).str[:10]
    new["trade_date"] = new["trade_date"].astype(str).str[:10]
    drop_dates = set(new["trade_date"])
    kept = old[~old["trade_date"].isin(drop_dates)]
    out = pd.concat([kept, new], ignore_index=True, sort=False)
    return out.sort_values("trade_date").drop_duplicates("trade_date", keep="last").reset_index(drop=True)


def export_regime(
    *,
    from_date: str = "",
    to_date: str = "",
    out_csv: Path = OUT_CSV,
    fetch_csi: bool = False,
    incremental: bool = False,
    min_bars: int = DEFAULT_MIN_LISTED_TD,
    min_universe: int = DEFAULT_MIN_UNIVERSE,
    cache_dir: Path = DAILY_FULL,
) -> Dict[str, Any]:
    today = datetime.now().strftime("%Y-%m-%d")
    if incremental:
        fetch_csi = True
        to_date = to_date or today
        if not from_date:
            from_date = (datetime.now() - timedelta(days=INCREMENTAL_LOOKBACK_CALENDAR_DAYS)).strftime(
                "%Y-%m-%d"
            )

    if fetch_csi or not INDEX_CACHE.is_file():
        from tools.fetch_csi_all_share_daily import fetch_csi_all_share_daily, save_cache

        print("[export] fetching CSI 000985…")
        save_cache(fetch_csi_all_share_daily(), INDEX_CACHE)

    rules = load_rules()
    min_bars = int(rules.get("min_bars_for_ipo_filter") or min_bars)
    min_universe = int(rules.get("min_universe") or min_universe)

    print(
        "[export] computing breadth panel… from=%s to=%s cache=%s"
        % (from_date or "(csi start)", to_date or "(csi end)", cache_dir)
    )
    panel = compute_breadth_panel(
        from_date=from_date or None,
        to_date=to_date or None,
        min_listed_trading_days=min_bars,
        min_universe=min_universe,
        cache_dir=cache_dir,
    )
    if panel.empty:
        raise SystemExit("empty panel — check daily_full / date range")

    labeled = apply_regime_labels(panel, rules)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    for old in out_csv.parent.glob("*.jsonl"):
        old.unlink()

    if incremental and out_csv.is_file():
        try:
            prev = pd.read_csv(out_csv, dtype={"trade_date": str, "trade_date_ymd": str})
        except Exception:
            prev = pd.DataFrame()
        labeled = _merge_by_trade_date(prev, labeled)
        print("[export] incremental merge → total rows=%d" % len(labeled))

    tmp_csv = out_csv.with_suffix(out_csv.suffix + ".tmp")
    labeled.to_csv(tmp_csv, index=False, encoding="utf-8-sig")
    try:
        tmp_csv.replace(out_csv)
    except OSError as e:
        fallback = out_csv.with_name(out_csv.stem + ".new.csv")
        try:
            if fallback.exists():
                fallback.unlink()
            tmp_csv.replace(fallback)
        except OSError:
            raise e from None
        print(
            "[export] WARN: 无法覆盖 %s（文件可能被 Excel 占用），已写入 %s"
            % (out_csv, fallback)
        )
        out_csv = fallback

    meta = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": "incremental" if incremental else "full",
        "csi_code": "000985.SH",
        "csi_note": "中证全指收盘价；禁止上证综指",
        "ipo_filter": "local daily bar count < min_bars excluded",
        "min_bars_for_ipo_filter": min_bars,
        "min_universe": min_universe,
        "rule_version": rules.get("rule_version"),
        "feature_version": str(labeled["feature_version"].iloc[0])
        if "feature_version" in labeled.columns
        else "",
        "rows": int(len(labeled)),
        "date_from": str(labeled["trade_date"].iloc[0]),
        "date_to": str(labeled["trade_date"].iloc[-1]),
        "breadth_ok_days": int(labeled["breadth_ok"].sum())
        if "breadth_ok" in labeled.columns
        else 0,
        "out_csv": str(out_csv),
        "sample_labels": labeled["label_zh"].tail(5).tolist()
        if "label_zh" in labeled.columns
        else [],
    }
    meta_path = out_csv.parent / "market_regime_export_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "[export] rows=%d %s..%s breadth_ok_days=%s"
        % (meta["rows"], meta["date_from"], meta["date_to"], meta["breadth_ok_days"])
    )
    print("[export] csv", out_csv)
    for lab in meta["sample_labels"]:
        print("  ", lab)
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description="Export market regime daily CSV")
    ap.add_argument("--from-date", default="")
    ap.add_argument("--to-date", default="")
    ap.add_argument("--fetch-csi", action="store_true")
    ap.add_argument(
        "--incremental",
        action="store_true",
        help="日更：刷新 CSI，重算近窗口并合并进已有 CSV",
    )
    ap.add_argument("--out", default=str(OUT_CSV), help="输出 CSV 路径")
    ap.add_argument(
        "--cache-dir",
        default=str(DAILY_FULL),
        help="个股日线目录（默认 data/daily_full）",
    )
    args = ap.parse_args()
    export_regime(
        from_date=args.from_date,
        to_date=args.to_date,
        out_csv=Path(args.out),
        fetch_csi=bool(args.fetch_csi),
        incremental=bool(args.incremental),
        cache_dir=Path(args.cache_dir),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
