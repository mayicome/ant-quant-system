# -*- coding: utf-8 -*-
"""拉取中证全指（000985）日线并缓存到 data/index_cache/000985.SH.csv。

数据源：腾讯 ak.stock_zh_index_daily_tx(symbol='sh000985')。
禁止使用上证综指 000001。

用法：
  python tools/fetch_csi_all_share_daily.py
  python tools/fetch_csi_all_share_daily.py --force
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "data" / "index_cache"
OUT_CSV = OUT_DIR / "000985.SH.csv"
SYMBOL = "sh000985"
CODE = "000985.SH"


def fetch_csi_all_share_daily() -> pd.DataFrame:
    import akshare as ak

    df = ak.stock_zh_index_daily_tx(symbol=SYMBOL)
    if df is None or df.empty:
        raise RuntimeError("empty response from stock_zh_index_daily_tx(%s)" % SYMBOL)
    df = df.copy()
    # normalize columns
    colmap = {c: str(c).strip().lower() for c in df.columns}
    df = df.rename(columns=colmap)
    if "date" not in df.columns:
        raise RuntimeError("missing date column: %s" % list(df.columns))
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for c in ("open", "high", "low", "close", "amount", "volume"):
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    keep = [c for c in ("date", "open", "high", "low", "close", "volume", "amount") if c in df.columns]
    df = df.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date", keep="last")
    df = df.loc[:, keep].reset_index(drop=True)
    df.insert(0, "code", CODE)
    return df


def save_cache(df: pd.DataFrame, path: Path = OUT_CSV) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def load_cache(path: Path = OUT_CSV) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame()
    df = pd.read_csv(path, dtype={"code": str})
    return df


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch CSI All Share (000985) daily bars")
    ap.add_argument("--force", action="store_true", help="重新拉取覆盖本地缓存")
    ap.add_argument("--out", default=str(OUT_CSV))
    args = ap.parse_args()
    out = Path(args.out)
    if out.name.upper().startswith("000001"):
        raise SystemExit("拒绝写入上证综指路径: %s" % out)

    if out.is_file() and not args.force:
        old = load_cache(out)
        print("[csi] cache exists rows=%d range=%s..%s (%s)" % (
            len(old),
            old["date"].iloc[0] if len(old) else "",
            old["date"].iloc[-1] if len(old) else "",
            out,
        ))
        print("[csi] use --force to refresh")
        return 0

    print("[csi] fetching", SYMBOL, "via腾讯…")
    df = fetch_csi_all_share_daily()
    save_cache(df, out)
    meta = {
        "code": CODE,
        "symbol": SYMBOL,
        "source": "ak.stock_zh_index_daily_tx",
        "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rows": len(df),
        "date_from": str(df["date"].iloc[0]),
        "date_to": str(df["date"].iloc[-1]),
        "note": "中证全指；禁止用 000001 上证综指",
    }
    meta_path = out.with_suffix(".meta.json")
    import json

    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("[csi] saved", out, "rows=%d %s..%s" % (len(df), meta["date_from"], meta["date_to"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
