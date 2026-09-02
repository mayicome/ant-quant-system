# -*- coding: utf-8 -*-
"""全 A 市场宽度：ADR / UDR / UDV / TRIN（日值 + ma5/ma10）。

宇宙：沪深可交易 A 股；剔除 ST、新股（默认上市未满 60 交易日）、停牌。
价格序列请用中证全指 000985，勿用上证综指。
"""
from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DAILY_CACHE = ROOT / "data" / "daily_cache"
DAILY_FULL = ROOT / "data" / "daily_full"
INDEX_CACHE = ROOT / "data" / "index_cache" / "000985.SH.csv"
STOCKS_CSV = ROOT / "data" / "all_a_stocks.csv"
STOCK_INFO_JSON = ROOT / "data" / "all_a_stock_info.json"

FEATURE_VERSION = "1.0.0"
RATIO_CAP = 99.0
DEFAULT_MIN_LISTED_TD = 60
DEFAULT_MIN_UNIVERSE = 3000

_CODE_RE = re.compile(r"^(\d{6})\.(SH|SZ)$", re.I)


def is_st_name(name: Any) -> bool:
    return "ST" in str(name or "").upper()


def is_hs_a_cache_file(path: Path) -> bool:
    m = _CODE_RE.match(path.name.replace(".csv", ""))
    if not m:
        return False
    # 排除指数：000xxx.SH 里 000001 等指数不在 daily_cache 个股命名惯例；
    # 个股沪市主板 60、科创 688；深市 00/30。daily_cache 里 000985.SZ 是个股。
    code6, mkt = m.group(1), m.group(2).upper()
    if mkt == "BJ":
        return False
    if mkt == "SH" and code6.startswith(("000", "399")):
        # 沪市指数代码段，不应出现在个股缓存；若误入则剔除
        return False
    if mkt == "SZ" and code6.startswith("399"):
        return False
    return True


def load_name_map() -> Dict[str, str]:
    out: Dict[str, str] = {}
    if STOCKS_CSV.is_file():
        try:
            with STOCKS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                fields = list(reader.fieldnames or [])
                code_k = fields[0] if fields else None
                name_k = fields[1] if len(fields) > 1 else None
                for row in reader:
                    code6 = "".join(ch for ch in str(row.get(code_k) or "") if ch.isdigit())[-6:].zfill(6)
                    if len(code6) != 6:
                        continue
                    out[code6] = str(row.get(name_k) or "").strip()
        except Exception:
            pass
    if STOCK_INFO_JSON.is_file():
        try:
            raw = json.loads(STOCK_INFO_JSON.read_text(encoding="utf-8"))
            stocks = raw.get("stocks") if isinstance(raw, dict) else raw
            if isinstance(stocks, dict):
                for k, v in stocks.items():
                    if not isinstance(v, dict):
                        continue
                    code6 = "".join(ch for ch in str(v.get("stock_code") or k) if ch.isdigit())[-6:].zfill(6)
                    if len(code6) != 6:
                        continue
                    nm = str(v.get("name") or "").strip()
                    if nm:
                        out[code6] = nm
        except Exception:
            pass
    return out


def load_csi_close_series(path: Path = INDEX_CACHE) -> pd.DataFrame:
    """返回 date, close[, volume/amount]；断言非上证综指文件。"""
    if "000001" in path.name:
        raise ValueError("禁止使用上证综指路径: %s" % path)
    if not path.is_file():
        raise FileNotFoundError(
            "缺少中证全指缓存 %s，请先运行: python tools/fetch_csi_all_share_daily.py" % path
        )
    df = pd.read_csv(path)
    if "code" in df.columns and df["code"].astype(str).str.contains("000001").any():
        raise ValueError("缓存疑似上证综指，拒绝使用")
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    if "amount" in df.columns:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values("date").drop_duplicates("date", keep="last")
    return df.reset_index(drop=True)


def _cap_ratio(num: float, den: float, cap: float = RATIO_CAP) -> Optional[float]:
    if den is None or den == 0 or pd.isna(den):
        if num and num > 0:
            return float(cap)
        return None
    if pd.isna(num):
        return None
    r = float(num) / float(den)
    if r > cap:
        return float(cap)
    return r


def list_hs_daily_cache_files(cache_dir: Path = DAILY_CACHE) -> List[Path]:
    if not cache_dir.is_dir():
        return []
    return sorted(p for p in cache_dir.glob("*.csv") if is_hs_a_cache_file(p))


def _eligible_codes(
    files: List[Path],
    names: Dict[str, str],
) -> List[Tuple[str, Path]]:
    out: List[Tuple[str, Path]] = []
    for p in files:
        code_full = p.name[: -len(".csv")]
        code6 = code_full.split(".", 1)[0]
        if is_st_name(names.get(code6, "")):
            continue
        out.append((code_full, p))
    return out


def _read_stock_ohlcv(path: Path) -> Optional[pd.DataFrame]:
    try:
        df = pd.read_csv(path, usecols=lambda c: str(c).lower() in ("date", "close", "volume", "amount"))
    except Exception:
        try:
            df = pd.read_csv(path)
        except Exception:
            return None
    cols = {c: str(c).strip().lower() for c in df.columns}
    df = df.rename(columns=cols)
    if "date" not in df.columns or "close" not in df.columns:
        return None
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    else:
        df["volume"] = pd.NA
    if "amount" in df.columns:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    df = df.dropna(subset=["date", "close"])
    df = df[df["close"] > 0]
    if df.empty:
        return None
    return df.sort_values("date").drop_duplicates("date", keep="last")


def build_trading_day_index(csi: pd.DataFrame) -> List[str]:
    return list(csi["date"].astype(str))


def compute_breadth_panel(
    *,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    min_listed_trading_days: int = DEFAULT_MIN_LISTED_TD,
    min_universe: int = DEFAULT_MIN_UNIVERSE,
    cache_dir: Path = DAILY_CACHE,
    index_path: Path = INDEX_CACHE,
    progress_every: int = 500,
) -> pd.DataFrame:
    """返回每个交易日一行的宽度 + 中证全指价量特征（含滚动）。

    新股：用该票本地日线截至当日的已有根数 < min_listed_trading_days 则剔除
    （不依赖 all_a_stocks 上市日，避免名单不全）。
    """
    csi = load_csi_close_series(index_path)
    if from_date:
        csi = csi[csi["date"] >= from_date[:10]]
    if to_date:
        csi = csi[csi["date"] <= to_date[:10]]
    if csi.empty:
        return pd.DataFrame()

    trade_days = build_trading_day_index(csi)
    day_set = set(trade_days)

    names = load_name_map()
    files = list_hs_daily_cache_files(cache_dir)
    eligible = _eligible_codes(files, names)

    n_up = {d: 0 for d in trade_days}
    n_dn = {d: 0 for d in trade_days}
    n_flat = {d: 0 for d in trade_days}
    v_up = {d: 0.0 for d in trade_days}
    v_dn = {d: 0.0 for d in trade_days}
    n_uni = {d: 0 for d in trade_days}
    min_bars = int(min_listed_trading_days)

    for i, (code_full, path) in enumerate(eligible, 1):
        if progress_every and i % progress_every == 0:
            print("[breadth] loaded %d/%d" % (i, len(eligible)))
        df = _read_stock_ohlcv(path)
        if df is None or len(df) < 2:
            continue

        # 用全量日线序（不仅窗口内）判定「截至当日已有多少根」
        all_dates = df["date"].tolist()
        all_closes = df["close"].to_numpy()
        all_vols = df["volume"].to_numpy() if "volume" in df.columns else None

        for j, d in enumerate(all_dates):
            if d not in day_set:
                continue
            if j < 1:
                continue
            # 新股：截至当日（含）本地日线根数不足
            bars_so_far = j + 1
            if bars_so_far < min_bars:
                continue
            px = float(all_closes[j])
            pc = float(all_closes[j - 1])
            if pc <= 0 or px <= 0:
                continue
            vol = float(all_vols[j]) if all_vols is not None and not pd.isna(all_vols[j]) else 0.0
            # 停牌：无量且价格不动
            if vol <= 0 and abs(px - pc) < 1e-12:
                continue

            n_uni[d] += 1
            chg = px - pc
            if chg > 1e-12:
                n_up[d] += 1
                v_up[d] += max(vol, 0.0)
            elif chg < -1e-12:
                n_dn[d] += 1
                v_dn[d] += max(vol, 0.0)
            else:
                n_flat[d] += 1

    rows: List[Dict[str, Any]] = []
    csi_by_date = csi.set_index("date")
    for d in trade_days:
        a, dn = n_up[d], n_dn[d]
        vu, vd = v_up[d], v_dn[d]
        adr = _cap_ratio(a, dn)
        udr = _cap_ratio(vu, vd)
        udv = vu - vd
        trin = None
        if adr is not None and udr is not None and udr != 0:
            trin = _cap_ratio(adr, udr)
        elif adr is not None and (udr is None or udr == 0):
            trin = RATIO_CAP if a > 0 else None

        crow = csi_by_date.loc[d] if d in csi_by_date.index else None
        csi_close = float(crow["close"]) if crow is not None else None
        csi_vol = None
        if crow is not None:
            if "volume" in crow.index and not pd.isna(crow.get("volume")):
                csi_vol = float(crow["volume"])
            elif "amount" in crow.index and not pd.isna(crow.get("amount")):
                csi_vol = float(crow["amount"])

        uni = n_uni[d]
        rows.append(
            {
                "trade_date": d,
                "trade_date_ymd": d.replace("-", ""),
                "n_universe": uni,
                "n_up": a,
                "n_down": dn,
                "n_flat": n_flat[d],
                "vol_up": vu,
                "vol_down": vd,
                "ADR": adr,
                "UDR": udr,
                "UDV": udv,
                "TRIN": trin,
                "csi_code": "000985.SH",
                "csi_close": csi_close,
                "csi_volume": csi_vol,
                "breadth_ok": bool(uni >= int(min_universe)),
                "feature_version": FEATURE_VERSION,
                "min_bars_for_ipo_filter": min_bars,
            }
        )

    out = pd.DataFrame(rows)
    if out.empty:
        return out

    out["csi_ret_1d"] = out["csi_close"].pct_change()

    for col in ("ADR", "UDR", "UDV", "TRIN", "csi_close", "csi_volume"):
        if col not in out.columns:
            continue
        out["%s_ma5" % col] = out[col].rolling(5, min_periods=1).mean()
        out["%s_ma10" % col] = out[col].rolling(10, min_periods=1).mean()

    return out
