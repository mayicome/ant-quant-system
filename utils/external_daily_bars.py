# -*- coding: utf-8 -*-
"""公开日线：东财为主，腾讯、同花顺网站兜底。

口径与 data/daily_cache 一致：不复权，成交量单位为「手」，成交额为元。
同花顺原始成交量是「股」，写入前除以 100。
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import requests

from utils.http_ipv4 import install_prefer_ipv4, as_timeout, prefer_ipv4

from utils.daily_cache_reader import to_full_stock_code

OUT_COLS = ("date", "open", "high", "low", "close", "volume", "amount")

_EM_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
_EM_UT = "7eea3edcaed734bea9cbfc24409ed989"
_TX_URL = "https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get"
_THS_URL = "https://d.10jqka.com.cn/v6/line/{key}/01/last.js"


try:
    install_prefer_ipv4()
except Exception:
    pass

def _session() -> requests.Session:
    os.environ.setdefault("NO_PROXY", "*")
    sess = requests.Session()
    sess.trust_env = False
    sess.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
        }
    )
    return sess


def _ymd(value: str) -> str:
    return str(value or "").replace("-", "")[:8]


def _iso(ymd: str) -> str:
    ymd = _ymd(ymd)
    return f"{ymd[:4]}-{ymd[4:6]}-{ymd[6:8]}"


def _f(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bar(
    ymd: str,
    open_: Any,
    high: Any,
    low: Any,
    close: Any,
    volume_lots: Any,
    amount: Any,
    source: str,
) -> Optional[Dict[str, Any]]:
    o, h, l, c = _f(open_), _f(high), _f(low), _f(close)
    vol = _f(volume_lots)
    if not _ymd(ymd) or c is None or c <= 0 or vol is None or vol < 0:
        return None
    amt = _f(amount)
    return {
        "date": _iso(ymd),
        "open": o if o is not None else c,
        "high": h if h is not None else c,
        "low": l if l is not None else c,
        "close": c,
        "volume": float(round(vol)),
        "amount": None if amt is None else float(amt),
        "source": source,
    }


def _in_range(bar: Dict[str, Any], start: str, end: str) -> bool:
    ymd = bar["date"].replace("-", "")
    if start and ymd < _ymd(start):
        return False
    if end and ymd > _ymd(end):
        return False
    return True


def _split_code(stock_code: str) -> Tuple[str, str]:
    full = to_full_stock_code(stock_code)
    num, mkt = full.split(".", 1)
    return num, mkt


def fetch_eastmoney(
    stock_code: str,
    start: str,
    end: str,
    session: Optional[requests.Session] = None,
    timeout: float = 20.0,
) -> List[Dict[str, Any]]:
    """东财日 K。返回列顺序在接口里是 open,close,high,low。成交量已是手。"""
    num, mkt = _split_code(stock_code)
    secid = f"1.{num}" if mkt == "SH" else f"0.{num}"
    sess = session or _session()
    with prefer_ipv4():
        resp = sess.get(
            _EM_URL,
            params={
                "fields1": "f1,f2,f3,f4,f5,f6",
                "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
                "ut": _EM_UT,
                "klt": "101",
                "fqt": "0",
                "secid": secid,
                "beg": _ymd(start) or "19900101",
                "end": _ymd(end) or datetime.now().strftime("%Y%m%d"),
            },
            headers={"Referer": "https://quote.eastmoney.com/"},
            timeout=as_timeout(timeout),
        )
    resp.raise_for_status()
    klines = ((resp.json() or {}).get("data") or {}).get("klines") or []
    out: List[Dict[str, Any]] = []
    for line in klines:
        parts = str(line).split(",")
        if len(parts) < 7:
            continue
        bar = _bar(parts[0], parts[1], parts[3], parts[4], parts[2], parts[5], parts[6], "eastmoney")
        if bar and _in_range(bar, start, end):
            out.append(bar)
    return out


def fetch_tencent(
    stock_code: str,
    start: str,
    end: str,
    session: Optional[requests.Session] = None,
    timeout: float = 20.0,
) -> List[Dict[str, Any]]:
    """腾讯不复权日 K。行内顺序是 open,close,high,low，成交量是手，没有成交额。"""
    num, mkt = _split_code(stock_code)
    prefix = {"SH": "sh", "SZ": "sz", "BJ": "bj"}.get(mkt, "sz")
    symbol = prefix + num
    # 接口按根数返回，多取一些再按日期裁。
    sess = session or _session()
    # 旧 fqkline 已 501；newfqkline 为现行接口。保留 fallback。
    tx_urls = (
        _TX_URL,
        "https://web.ifzq.gtimg.cn/appstock/app/newfqkline/get",
        "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
    )
    node = {}
    last_exc = None
    for url in tx_urls:
        try:
            with prefer_ipv4():
                resp = sess.get(
                    url,
                    params={"param": f"{symbol},day,,,320,"},
                    timeout=as_timeout(timeout),
                )
            resp.raise_for_status()
            node = ((resp.json() or {}).get("data") or {}).get(symbol) or {}
            if node:
                break
        except Exception as exc:
            last_exc = exc
            node = {}
    if not node and last_exc is not None:
        raise last_exc
    rows = node.get("day") or node.get("qfqday") or []
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 6:
            continue
        vol = row[5]
        # 科创板这里给的是股，其它板块是手。
        if mkt == "SH" and num.startswith(("688", "689")):
            fv = _f(vol)
            vol = None if fv is None else fv / 100.0
        bar = _bar(row[0], row[1], row[3], row[4], row[2], vol, None, "tencent")
        if bar and _in_range(bar, start, end):
            out.append(bar)
    if out and out[-1].get("amount") is None:
        quote = ((node.get("qt") or {}).get(symbol)) if isinstance(node.get("qt"), dict) else None
        if isinstance(quote, list) and len(quote) > 35:
            bits = str(quote[35] or "").split("/")
            qday = str(quote[30] or "")[:8]
            amt = _f(bits[2]) if len(bits) >= 3 else None
            if amt and amt > 0 and out[-1]["date"].replace("-", "") == qday:
                out[-1]["amount"] = float(amt)
    return out


def fetch_ths(
    stock_code: str,
    start: str,
    end: str,
    session: Optional[requests.Session] = None,
    timeout: float = 20.0,
) -> List[Dict[str, Any]]:
    """同花顺网站最近一段不复权日 K。成交量是股，这里换成手。"""
    num, mkt = _split_code(stock_code)
    key = f"bj_{num}" if mkt == "BJ" else f"hs_{num}"
    sess = session or _session()
    with prefer_ipv4():
        resp = sess.get(
            _THS_URL.format(key=key),
            headers={"Referer": "https://q.10jqka.com.cn/"},
            timeout=as_timeout(timeout),
        )
    resp.raise_for_status()
    text = resp.text
    payload = json.loads(text[text.find("(") + 1 : text.rfind(")")])
    raw = str(payload.get("data") or "")
    out: List[Dict[str, Any]] = []
    for line in raw.split(";"):
        parts = line.split(",")
        if len(parts) < 6:
            continue
        vol_shares = _f(parts[5])
        vol_lots = None if vol_shares is None else vol_shares / 100.0
        amount = parts[6] if len(parts) > 6 else None
        # 日期,开,高,低,收,量,额
        bar = _bar(parts[0], parts[1], parts[2], parts[3], parts[4], vol_lots, amount, "ths")
        if bar and _in_range(bar, start, end):
            out.append(bar)
    return out


_FETCHERS = (
    ("eastmoney", fetch_eastmoney),
    ("tencent", fetch_tencent),
    ("ths", fetch_ths),
)



def _px_close_enough(a: float, b: float, rel: float = 0.002, abs_tol: float = 0.015) -> bool:
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) <= max(abs_tol, rel * max(a, b))


def _ohlc_agree(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return all(
        _px_close_enough(float(a[k]), float(b[k]))
        for k in ("open", "high", "low", "close")
    )


def fetch_day_bar_consensus(
    stock_code: str,
    day: str,
    session: Optional[requests.Session] = None,
    *,
    min_agree: int = 2,
    retries: int = 2,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Single-day OHLC with multi-source agreement.

    Fetches eastmoney / tencent / ths for that day (volume>0), each with light retries.
    - If >= min_agree sources agree on OHLC, return consensus bar; source like "eastmoney+ths".
    - If only one source has data, return that bar with "|solo" suffix (caller decides overwrite).
    - If sources disagree or all empty, return (None, reason).

    New append may use solo; overwrite should prefer multi-source (no |solo).
    """
    day = str(day or "")[:10]
    if len(day) < 10:
        return None, "bad_day"
    ymd = day.replace("-", "")
    sess = session or _session()
    got: List[Tuple[str, Dict[str, Any]]] = []
    errors: List[str] = []
    for name, fn in _FETCHERS:
        hit = None
        last_err = ""
        for attempt in range(max(1, int(retries))):
            try:
                rows = fn(stock_code, ymd, ymd, session=sess)
            except Exception as exc:
                last_err = "%s:%s" % (name, type(exc).__name__)
                continue
            for bar in rows or []:
                if str(bar.get("date") or "")[:10] == day and float(bar.get("volume") or 0) > 0:
                    hit = bar
                    break
            if hit is not None:
                break
            last_err = "%s:empty" % name
        if hit is None:
            errors.append(last_err or ("%s:empty" % name))
            continue
        got.append((name, hit))

    if not got:
        return None, ";".join(errors) or "empty"

    if len(got) == 1:
        name, bar = got[0]
        out = dict(bar)
        out["source"] = name + "|solo"
        return out, name + "|solo"

    best: List[Tuple[str, Dict[str, Any]]] = []
    for i, (n1, b1) in enumerate(got):
        group = [(n1, b1)]
        for j, (n2, b2) in enumerate(got):
            if i == j:
                continue
            if _ohlc_agree(b1, b2):
                group.append((n2, b2))
        if len(group) > len(best):
            best = group

    if len(best) < max(2, int(min_agree)):
        names = "+".join(n for n, _ in got)
        return None, "disagree:%s" % names

    names = "+".join(n for n, _ in best)
    out = dict(best[0][1])
    out["source"] = names
    return out, names


def looks_like_incomplete_eod(local: Dict[str, Any], ext: Dict[str, Any]) -> bool:
    """Local bar looks like an intraday snapshot vs a fuller external EOD bar."""
    try:
        lo, lh, ll, lc = float(local["open"]), float(local["high"]), float(local["low"]), float(local["close"])
        eo, eh, el, ec = float(ext["open"]), float(ext["high"]), float(ext["low"]), float(ext["close"])
    except (KeyError, TypeError, ValueError):
        return False
    if not _px_close_enough(lo, eo):
        return False
    # open matches; high/close materially lower on local (classic early freeze)
    high_low = lh < eh and (eh - lh) > max(0.02, 0.005 * eh)
    close_low = lc < ec and (ec - lc) > max(0.02, 0.005 * ec)
    return bool(high_low or close_low)


def fetch_daily_bars(
    stock_code: str,
    start: str,
    end: str,
    session: Optional[requests.Session] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """按东财 → 腾讯 → 同花顺尝试。返回 (K线, 来源名)；全部失败则来源为错误摘要。"""
    sess = session or _session()
    errors: List[str] = []
    for name, fn in _FETCHERS:
        try:
            rows = fn(stock_code, start, end, session=sess)
        except Exception as exc:
            errors.append(f"{name}:{type(exc).__name__}")
            continue
        if rows:
            return rows, name
        errors.append(f"{name}:empty")
    return [], ";".join(errors)
