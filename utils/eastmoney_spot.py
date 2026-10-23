# -*- coding: utf-8 -*-
"""东财 push2 现价，只给界面显示。下单仍用 QMT 行情。"""
from __future__ import annotations

import os
from typing import Dict, Iterable, List

import requests

from utils.daily_cache_reader import to_full_stock_code

_URLS = (
    "https://push2.eastmoney.com/api/qt/ulist.np/get",
    "https://82.push2.eastmoney.com/api/qt/ulist.np/get",
)
_UT = "b2884a393a59ad64002292a3e90d46a5"
_FIELDS = "f2,f12,f13,f14,f15,f16,f17,f18"


def _session() -> requests.Session:
    os.environ.setdefault("NO_PROXY", "*")
    sess = requests.Session()
    sess.trust_env = False
    sess.headers.update(
        {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            "Referer": "https://quote.eastmoney.com/",
        }
    )
    return sess


def _secid(code: str) -> str:
    full = to_full_stock_code(code)
    num, _, market = full.partition(".")
    # 东财 secid：沪 1.代码，深/北 0.代码
    return ("1." if market == "SH" else "0.") + num


def _num(raw) -> float:
    try:
        if raw in (None, "", "-"):
            return 0.0
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def fetch_spot_quotes(codes: Iterable[str], timeout: float = 3.0) -> Dict[str, Dict[str, float]]:
    """返回 {完整代码: price/open/high/low/pre_close/name}。失败返回空字典。"""
    wanted: List[str] = []
    seen = set()
    secids = []
    for raw in codes:
        full = to_full_stock_code(str(raw or ""))
        num = full.split(".")[0]
        if not num.isdigit() or len(num) != 6 or full in seen:
            continue
        seen.add(full)
        wanted.append(full)
        secids.append(_secid(full))
    if not secids:
        return {}
    params = {
        "fltt": "2",
        "invt": "2",
        "fields": _FIELDS,
        "secids": ",".join(secids[:80]),
        "ut": _UT,
    }
    payload = None
    from utils.http_ipv4 import as_timeout, prefer_ipv4

    to = as_timeout(timeout)
    sess = _session()
    with prefer_ipv4():
        for url in _URLS:
            try:
                resp = sess.get(url, params=params, timeout=to)
                resp.raise_for_status()
                payload = resp.json()
                break
            except (OSError, ValueError, requests.RequestException):
                payload = None
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data") or {}
    diff = data.get("diff") if isinstance(data, dict) else None
    if not isinstance(diff, list):
        return {}
    by_num = {code.split(".")[0]: code for code in wanted}
    out: Dict[str, Dict[str, float]] = {}
    for row in diff:
        if not isinstance(row, dict):
            continue
        num = str(row.get("f12") or "").zfill(6)
        full = by_num.get(num)
        if not full:
            continue
        price = _num(row.get("f2"))
        if price <= 0:
            continue
        out[full] = {
            "price": price,
            "high": _num(row.get("f15")),
            "low": _num(row.get("f16")),
            "open": _num(row.get("f17")),
            "pre_close": _num(row.get("f18")),
            "name": str(row.get("f14") or "").strip(),
        }
    return out
