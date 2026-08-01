#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美股中概核心标的 + 港股相关 ADR 隔夜涨跌幅（相对前一美股交易日收盘）

数据来源：Yahoo Finance（需安装 yfinance、pandas）
  pip install yfinance pandas

说明：
- 「隔夜」口径：当前可用最新价（盘前/盘中/盘后，以接口返回为准）相对 regularMarketPreviousClose。
- 非交易时段可能回落到昨收，涨跌幅接近 0。
- 标的池可在下方列表中自行增删；B 站美股代码为 BILI（非 BILIBILI）。
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
import time
import requests

# ===================== 1. 自定义中概 + 港股 ADR 标的池 =====================
CHINA_CONCEPT_CORE: List[Tuple[str, str]] = [
    # (代码, 备注)
    ("BABA", "阿里巴巴"),
    ("PDD", "拼多多"),
    ("JD", "京东"),
    ("TME", "腾讯音乐"),
    ("NTES", "网易"),
    ("BILI", "哔哩哔哩"),
    ("NIO", "蔚来"),
    ("XPEV", "小鹏"),
    ("LI", "理想"),
    ("TAL", "好未来"),
    ("EDU", "新东方"),
    ("BEKE", "贝壳"),
    ("DADA", "达达"),
    ("ZLAB", "再鼎医药"),
]

# 港股公司在美上市 ADR / 存托凭证（示例，可按需改）
HK_ADR_LIST: List[Tuple[str, str]] = [
    ("HSBC", "汇丰控股 ADR"),
    ("HNGGY", "恒生银行相关 ADR（请核对代码）"),
    ("CEKY", "ADR（请核对代码与名称）"),
    ("SMFG", "三井住友金融 ADR"),
]

# 中概/中国相关 ETF（整体情绪）
INDEX_ETFS: List[Tuple[str, str]] = [
    ("KWEB", "中概互联网 ETF"),
    ("CXSE", "中国大盘 ETF"),
    ("MCHI", "MSCI 中国 ETF"),
]


def _try_import_yfinance():
    try:
        import yfinance as yf  # noqa: F401

        return True
    except ImportError:
        return False


def _try_import_akshare():
    try:
        import akshare as ak  # noqa: F401

        return True
    except ImportError:
        return False


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _extract_last_two_close(batch_df: Any, symbol: str) -> Tuple[Optional[float], Optional[float]]:
    """从批量日线里提取 symbol 的最近收盘价与前一收盘价。"""
    close_series = None
    if batch_df is None or getattr(batch_df, "empty", True):
        return None, None

    # 多标的一般是 MultiIndex 列：('Close', 'BABA')；单标的则可能是普通列
    cols = getattr(batch_df, "columns", None)
    if cols is None:
        return None, None

    if getattr(cols, "nlevels", 1) >= 2:
        if ("Close", symbol) in cols:
            close_series = batch_df[("Close", symbol)]
    else:
        if "Close" in cols:
            close_series = batch_df["Close"]

    if close_series is None:
        return None, None

    valid = close_series.dropna()
    if len(valid) < 2:
        return None, None

    prev_close = _to_float(valid.iloc[-2])
    latest_close = _to_float(valid.iloc[-1])
    return latest_close, prev_close


def _fetch_last_price_with_retry(symbol: str, retry: int = 2, sleep_sec: float = 0.6) -> Optional[float]:
    """
    轻量尝试获取实时/盘前盘后价格（fast_info），失败时回退为 None。
    这里不用 Ticker.info，显著降低被限流概率。
    """
    import yfinance as yf

    for i in range(retry + 1):
        try:
            fi = yf.Ticker(symbol).fast_info or {}
            price = _to_float(fi.get("lastPrice"))
            if price is not None and price > 0:
                return price
        except Exception:
            if i < retry:
                time.sleep(sleep_sec)
    return None


def _build_quote_from_batch(symbol: str, note: str, batch_df: Any) -> Dict[str, Any]:
    latest_close, prev_close = _extract_last_two_close(batch_df, symbol)

    # 优先使用 fast_info 的 lastPrice（更接近你说的隔夜口径），失败则回退到最新日线收盘
    last_price = _fetch_last_price_with_retry(symbol)
    if last_price is None:
        last_price = latest_close

    pct = None
    if prev_close is not None and prev_close > 0 and last_price is not None:
        pct = (last_price - prev_close) / prev_close * 100.0

    err = ""
    if prev_close is None:
        err = "缺少足够日线（至少2个交易日）"
    elif last_price is None:
        err = "无法获取最新价格"

    return {
        "symbol": symbol,
        "name": symbol,
        "price": last_price,
        "prev_close": prev_close,
        "chg_pct": pct,
        "currency": "USD",
        "market_state": "",
        "note": note,
        "error": err,
    }


def _fetch_board_akshare(rows: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
    """
    备用源：AkShare 美股实时行情。
    返回字段尽量与主流程一致；若无法获取则回填 error。
    """
    import akshare as ak

    out: List[Dict[str, Any]] = []
    try:
        spot = ak.stock_us_spot_em()
    except Exception as e:
        err_msg = f"akshare 拉取失败: {e}"
        for sym, note in rows:
            out.append(
                {
                    "symbol": sym,
                    "name": "",
                    "price": None,
                    "prev_close": None,
                    "chg_pct": None,
                    "currency": "",
                    "market_state": "",
                    "note": note,
                    "error": err_msg,
                }
            )
        return out

    if spot is None or getattr(spot, "empty", True):
        for sym, note in rows:
            out.append(
                {
                    "symbol": sym,
                    "name": "",
                    "price": None,
                    "prev_close": None,
                    "chg_pct": None,
                    "currency": "",
                    "market_state": "",
                    "note": note,
                    "error": "akshare 返回空数据",
                }
            )
        return out

    code_col = "代码" if "代码" in spot.columns else None
    price_col = "最新价" if "最新价" in spot.columns else None
    pct_col = "涨跌幅" if "涨跌幅" in spot.columns else None
    name_col = "名称" if "名称" in spot.columns else None

    if code_col is None or price_col is None:
        for sym, note in rows:
            out.append(
                {
                    "symbol": sym,
                    "name": "",
                    "price": None,
                    "prev_close": None,
                    "chg_pct": None,
                    "currency": "",
                    "market_state": "",
                    "note": note,
                    "error": "akshare 字段结构变化（缺少 代码/最新价）",
                }
            )
        return out

    # 建立 symbol -> row 映射（akshare 代码常见形态: 105.BABA）
    row_map: Dict[str, Any] = {}
    for _, row in spot.iterrows():
        raw_code = str(row.get(code_col, "")).strip().upper()
        if not raw_code:
            continue
        token = raw_code.split(".")[-1]
        if token:
            row_map[token] = row

    for sym, note in rows:
        symbol = sym.strip().upper()
        row = row_map.get(symbol)
        if row is None:
            out.append(
                {
                    "symbol": symbol,
                    "name": "",
                    "price": None,
                    "prev_close": None,
                    "chg_pct": None,
                    "currency": "",
                    "market_state": "",
                    "note": note,
                    "error": "akshare 未找到该代码",
                }
            )
            continue

        price = _to_float(row.get(price_col))
        pct = _to_float(row.get(pct_col)) if pct_col else None
        prev_close = None
        if price is not None and pct is not None and (100.0 + pct) != 0:
            prev_close = price / (1.0 + pct / 100.0)

        out.append(
            {
                "symbol": symbol,
                "name": str(row.get(name_col) if name_col else symbol)[:40],
                "price": price,
                "prev_close": prev_close,
                "chg_pct": pct,
                "currency": "USD",
                "market_state": "",
                "note": note,
                "error": "" if price is not None else "akshare 最新价为空",
            }
        )
    return out


def _fetch_board_eastmoney(rows: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
    """
    备用源：东方财富美股行情接口（直连 HTTP API）。
    用 latest + pct 反推昨收，避免依赖 Yahoo/AkShare。
    """
    out: List[Dict[str, Any]] = []
    url = "https://72.push2.eastmoney.com/api/qt/clist/get"
    params = {
        "pn": "1",
        "pz": "20000",
        "po": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "fid": "f3",
        "fs": "m:105,m:106,m:107",
        "fields": "f12,f14,f2,f3",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
    }

    try:
        # 某些环境证书链异常，关闭校验以提高可用性（仅行情拉取）
        resp = requests.get(url, params=params, headers=headers, timeout=10, verify=False)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        err_msg = f"eastmoney 拉取失败: {e}"
        for sym, note in rows:
            out.append(
                {
                    "symbol": sym,
                    "name": "",
                    "price": None,
                    "prev_close": None,
                    "chg_pct": None,
                    "currency": "",
                    "market_state": "",
                    "note": note,
                    "error": err_msg,
                }
            )
        return out

    diff = ((data or {}).get("data") or {}).get("diff") or []
    em_map: Dict[str, Dict[str, Any]] = {}
    for item in diff:
        sym = str(item.get("f12") or "").upper().strip()
        if not sym:
            continue
        em_map[sym] = item

    for sym, note in rows:
        symbol = sym.strip().upper()
        item = em_map.get(symbol)
        if not item:
            out.append(
                {
                    "symbol": symbol,
                    "name": "",
                    "price": None,
                    "prev_close": None,
                    "chg_pct": None,
                    "currency": "",
                    "market_state": "",
                    "note": note,
                    "error": "eastmoney 未找到该代码",
                }
            )
            continue

        price = _to_float(item.get("f2"))
        pct = _to_float(item.get("f3"))
        prev_close = None
        if price is not None and pct is not None and (100.0 + pct) != 0:
            prev_close = price / (1.0 + pct / 100.0)

        out.append(
            {
                "symbol": symbol,
                "name": str(item.get("f14") or symbol)[:40],
                "price": price,
                "prev_close": prev_close,
                "chg_pct": pct,
                "currency": "USD",
                "market_state": "",
                "note": note,
                "error": "" if price is not None else "eastmoney 最新价为空",
            }
        )
    return out


def fetch_board(rows: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
    if not _try_import_yfinance():
        if _try_import_akshare():
            ak_out = _fetch_board_akshare(rows)
            if any(not r.get("error") for r in ak_out):
                return ak_out
        em_out = _fetch_board_eastmoney(rows)
        if any(not r.get("error") for r in em_out):
            return em_out
        return [
            {
                "symbol": sym,
                "name": "",
                "price": None,
                "prev_close": None,
                "chg_pct": None,
                "currency": "",
                "market_state": "",
                "note": note,
                "error": "缺少可用数据源（yfinance/akshare/eastmoney）",
            }
            for sym, note in rows
        ]

    import yfinance as yf

    symbols = [sym.strip().upper() for sym, _ in rows]
    symbols = [s for s in symbols if s]
    out: List[Dict[str, Any]] = []
    if not symbols:
        return out

    try:
        # 批量下载，避免逐个 info 调用触发 429
        batch_df = yf.download(
            tickers=symbols,
            period="7d",
            interval="1d",
            auto_adjust=False,
            group_by="column",
            progress=False,
            threads=False,
        )
    except Exception as e:
        # 若批量请求都失败，仍返回逐行错误，避免静默
        err_msg = str(e)
        for sym, note in rows:
            out.append(
                {
                    "symbol": sym,
                    "name": "",
                    "price": None,
                    "prev_close": None,
                    "chg_pct": None,
                    "currency": "",
                    "market_state": "",
                    "note": note,
                    "error": err_msg,
                }
            )
        return out

    out: List[Dict[str, Any]] = []
    for sym, note in rows:
        try:
            r = _build_quote_from_batch(sym.strip().upper(), note, batch_df)
            out.append(r)
        except Exception as e:
            out.append(
                {
                    "symbol": sym,
                    "name": "",
                    "price": None,
                    "prev_close": None,
                    "chg_pct": None,
                    "currency": "",
                    "market_state": "",
                    "note": note,
                    "error": str(e),
                }
            )

    # 如果 Yahoo 全部失败，自动切换到 AkShare 备用数据源
    ok_count = sum(1 for r in out if not r.get("error"))
    if ok_count == 0:
        if _try_import_akshare():
            ak_out = _fetch_board_akshare(rows)
            if any(not r.get("error") for r in ak_out):
                return ak_out
        em_out = _fetch_board_eastmoney(rows)
        if any(not r.get("error") for r in em_out):
            return em_out
    return out


def _fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "-"
    return f"{x:+.2f}%"


def _fmt_px(x: Optional[float]) -> str:
    if x is None:
        return "-"
    return f"{x:.4f}"


def print_table(title: str, data: List[Dict[str, Any]]) -> None:
    print()
    print(f"=== {title} ===")
    if not data:
        print("(无数据)")
        return
    header = f"{'代码':<8} {'币种':<6} {'现价':>10} {'昨收':>10} {'涨跌%':>10} {'备注':<28}"
    print(header)
    print("-" * len(header))
    for r in data:
        err = r.get("error")
        if err:
            print(f"{r['symbol']:<8} {'ERR':<6} {'-':>10} {'-':>10} {'-':>10} {str(err)[:28]}")
            continue
        print(
            f"{r['symbol']:<8} {str(r.get('currency') or '-')[:6]:<6} "
            f"{_fmt_px(r.get('price')):>10} {_fmt_px(r.get('prev_close')):>10} "
            f"{_fmt_pct(r.get('chg_pct')):>10} {str(r.get('note') or '')[:28]:<28}"
        )


def save_csv(path: str, sections: List[Tuple[str, List[Dict[str, Any]]]]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(
            [
                "section",
                "symbol",
                "name",
                "price",
                "prev_close",
                "chg_pct",
                "currency",
                "market_state",
                "note",
                "error",
            ]
        )
        for sec_name, rows in sections:
            for r in rows:
                w.writerow(
                    [
                        sec_name,
                        r.get("symbol"),
                        r.get("name"),
                        r.get("price"),
                        r.get("prev_close"),
                        r.get("chg_pct"),
                        r.get("currency"),
                        r.get("market_state"),
                        r.get("note"),
                        r.get("error", ""),
                    ]
                )


def main() -> int:
    parser = argparse.ArgumentParser(description="中概核心 + 港股 ADR 隔夜涨跌幅（Yahoo）")
    parser.add_argument(
        "--csv",
        metavar="PATH",
        help="导出 CSV 路径（默认 history_data/american_adr_overnight_YYYYMMDD.csv）",
    )
    parser.add_argument(
        "--no-index",
        action="store_true",
        help="不拉取 KWEB/CXSE/MCHI 等 ETF",
    )
    parser.add_argument(
        "--no-hk-adr",
        action="store_true",
        help="不拉取 HK_ADR_LIST 段落",
    )
    args = parser.parse_args()

    if not _try_import_yfinance() and not _try_import_akshare():
        print("请先安装任一数据源依赖: pip install yfinance pandas  或  pip install akshare", file=sys.stderr)
        return 1

    sections: List[Tuple[str, List[Dict[str, Any]]]] = []

    d1 = fetch_board(CHINA_CONCEPT_CORE)
    sections.append(("中概核心标的", d1))
    print_table("中概核心标的", d1)

    if not args.no_hk_adr:
        d2 = fetch_board(HK_ADR_LIST)
        sections.append(("港股相关 ADR", d2))
        print_table("港股相关 ADR", d2)

    if not args.no_index:
        d3 = fetch_board(INDEX_ETFS)
        sections.append(("中概/中国相关 ETF", d3))
        print_table("中概/中国相关 ETF", d3)

    csv_path = args.csv
    if not csv_path:
        day = datetime.now().strftime("%Y%m%d")
        root = os.path.dirname(os.path.abspath(__file__))
        csv_path = os.path.join(root, "history_data", f"american_adr_overnight_{day}.csv")
    save_csv(csv_path, sections)
    print()
    print(f"已导出 CSV: {csv_path}")
    print(f"抓取时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
