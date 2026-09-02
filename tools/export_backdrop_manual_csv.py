# -*- coding: utf-8 -*-
"""导出人工趋势底色日度 CSV（不依赖涨跌家数，仅用中证全指日历）。

用法：
  python tools/export_backdrop_manual_csv.py
  python tools/export_backdrop_manual_csv.py --from-date 2025-01-01 --to-date 2026-12-31
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.market_regime_rules import classify_backdrop_manual, load_rules  # noqa: E402

CSI_CACHE = ROOT / "data" / "index_cache" / "000985.SH.csv"
OUT_DIR = ROOT / "data" / "market_regime"
OUT_CSV = OUT_DIR / "backdrop_daily.csv"


def export_backdrop(
    *,
    from_date: str = "2025-01-01",
    to_date: str = "",
    out_csv: Path = OUT_CSV,
) -> dict:
    rules = load_rules()
    csi = pd.read_csv(CSI_CACHE)
    csi["date"] = pd.to_datetime(csi["date"], errors="coerce")
    csi = csi.dropna(subset=["date", "close"]).sort_values("date")
    csi["close"] = pd.to_numeric(csi["close"], errors="coerce")
    csi = csi.dropna(subset=["close"])

    a = pd.Timestamp(from_date)
    b = pd.Timestamp(to_date) if to_date else csi["date"].max()
    csi = csi[(csi["date"] >= a) & (csi["date"] <= b)].copy().reset_index(drop=True)
    if csi.empty:
        raise SystemExit("无交易日数据")

    dates = csi["date"].dt.strftime("%Y-%m-%d")
    backdrops = classify_backdrop_manual(dates, rules)
    block_id = []
    bid = 0
    prev = None
    for lab in backdrops:
        if prev is None or lab != prev:
            bid += 1
            prev = lab
        block_id.append(bid)

    out = pd.DataFrame(
        {
            "trade_date": dates.tolist(),
            "backdrop": backdrops,
            "backdrop_block_id": block_id,
            "csi_close": csi["close"].astype(float).tolist(),
            "rule_version": str(rules.get("rule_version") or ""),
        }
    )
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

    # 连续块摘要
    blocks = (
        out.groupby("backdrop_block_id", as_index=False)
        .agg(
            from_date=("trade_date", "min"),
            to_date=("trade_date", "max"),
            backdrop=("backdrop", "first"),
            days=("trade_date", "count"),
        )
        .sort_values("backdrop_block_id")
    )
    meta = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "csi_code": "000985.SH",
        "rule_version": rules.get("rule_version"),
        "rows": int(len(out)),
        "date_from": str(out["trade_date"].iloc[0]),
        "date_to": str(out["trade_date"].iloc[-1]),
        "n_blocks": int(len(blocks)),
        "backdrop_counts": out["backdrop"].value_counts().to_dict(),
        "out_csv": str(out_csv),
        "blocks": blocks.to_dict(orient="records"),
    }
    meta_path = out_csv.parent / "backdrop_export_meta.json"
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        "[export] rows=%d %s..%s blocks=%d"
        % (meta["rows"], meta["date_from"], meta["date_to"], meta["n_blocks"])
    )
    print("[export] counts", meta["backdrop_counts"])
    print("[export] csv", out_csv)
    return meta


def main() -> int:
    ap = argparse.ArgumentParser(description="Export manual backdrop daily CSV")
    ap.add_argument("--from-date", default="2025-01-01")
    ap.add_argument("--to-date", default="")
    ap.add_argument("--out", default=str(OUT_CSV))
    args = ap.parse_args()
    export_backdrop(from_date=args.from_date, to_date=args.to_date, out_csv=Path(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
