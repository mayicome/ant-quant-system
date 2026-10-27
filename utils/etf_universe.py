# -*- coding: utf-8 -*-
"""A股场内 ETF 代码池。

优先东财 clist（多 host 重试）：MK0021~MK0024；
失败则回退 datacenter RPT_FUND_ETFLIST。
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

from utils.http_ipv4 import as_timeout, prefer_ipv4

ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_PATH = ROOT / "data" / "etf_universe.json"

_CLIST_PATH = "/api/qt/clist/get"
_CLIST_HOSTS = (
    "https://90.push2.eastmoney.com",
    "https://82.push2.eastmoney.com",
    "https://88.push2.eastmoney.com",
    "https://push2.eastmoney.com",
)
_FS = "b:MK0021,b:MK0022,b:MK0023,b:MK0024"
_FIELDS = "f12,f13,f14"
_SOURCE_CLIST = "eastmoney_clist_MK0021-24"
_SOURCE_DC = "eastmoney_datacenter_RPT_FUND_ETFLIST"
_DC_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"


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
            "Accept": "*/*",
        }
    )
    return sess


def _to_full(code6: str, market: Any) -> Optional[str]:
    code6 = str(code6 or "").strip()
    if len(code6) != 6 or not code6.isdigit():
        return None
    try:
        m = int(market)
    except (TypeError, ValueError):
        m = 1 if code6.startswith(("5", "6")) else 0
    suffix = "SH" if m == 1 else "SZ"
    return f"{code6}.{suffix}"


def _normalize_secucode(value: str) -> Optional[str]:
    text = str(value or "").strip().upper()
    if not text:
        return None
    if "." in text:
        num, mkt = text.split(".", 1)
        if len(num) == 6 and num.isdigit() and mkt in ("SH", "SZ"):
            return f"{num}.{mkt}"
    if len(text) == 6 and text.isdigit():
        return _to_full(text, 1 if text.startswith(("5", "6")) else 0)
    return None


def _fetch_clist_page(
    sess: requests.Session,
    pn: int,
    page_size: int,
    timeout: float,
) -> Dict[str, Any]:
    params = {
        "pn": pn,
        "pz": page_size,
        "po": 1,
        "np": 1,
        "fltt": 2,
        "invt": 2,
        "fid": "f12",
        "fs": _FS,
        "fields": _FIELDS,
    }
    last_exc: Optional[Exception] = None
    for host in _CLIST_HOSTS:
        for attempt in range(3):
            try:
                with prefer_ipv4():
                    resp = sess.get(
                        host + _CLIST_PATH,
                        params=params,
                        timeout=as_timeout(timeout),
                    )
                resp.raise_for_status()
                data = (resp.json() or {}).get("data") or {}
                if data:
                    return data
                last_exc = RuntimeError(f"{host}:empty_data")
            except Exception as exc:
                last_exc = exc
                time.sleep(0.4 * (attempt + 1))
    if last_exc:
        raise last_exc
    return {}


def _fetch_via_clist(
    sess: requests.Session,
    page_size: int,
    timeout: float,
) -> List[Dict[str, str]]:
    page_size = max(1, min(int(page_size), 100))
    items: List[Dict[str, str]] = []
    seen = set()
    pn = 1
    total = None
    while True:
        data = _fetch_clist_page(sess, pn, page_size, timeout)
        if total is None:
            try:
                total = int(data.get("total") or 0)
            except (TypeError, ValueError):
                total = 0
        diff = data.get("diff") or []
        if not diff:
            break
        for row in diff:
            if not isinstance(row, dict):
                continue
            full = _to_full(row.get("f12"), row.get("f13"))
            if not full or full in seen:
                continue
            seen.add(full)
            items.append(
                {
                    "code": full,
                    "name": str(row.get("f14") or "").strip(),
                    "market": full.split(".", 1)[1],
                }
            )
        if total and len(items) >= total:
            break
        if len(diff) < page_size:
            break
        pn += 1
        if pn > 200:
            break
    return items


def _fetch_via_datacenter(
    sess: requests.Session,
    page_size: int,
    timeout: float,
) -> List[Dict[str, str]]:
    page_size = max(1, min(int(page_size), 500))
    items: List[Dict[str, str]] = []
    seen = set()
    page = 1
    pages = None
    while True:
        params = {
            "sortColumns": "SECURITY_CODE",
            "sortTypes": "1",
            "reportName": "RPT_FUND_ETFLIST",
            "columns": "SECURITY_CODE,SECURITY_NAME_ABBR,SECUCODE",
            "pageSize": page_size,
            "pageNumber": page,
        }
        last_exc = None
        payload = None
        for attempt in range(4):
            try:
                with prefer_ipv4():
                    resp = sess.get(_DC_URL, params=params, timeout=as_timeout(timeout))
                resp.raise_for_status()
                payload = resp.json() or {}
                if not payload.get("success"):
                    raise RuntimeError(str(payload.get("message") or "datacenter_fail"))
                last_exc = None
                break
            except Exception as exc:
                last_exc = exc
                time.sleep(0.6 * (attempt + 1))
        if last_exc is not None:
            raise last_exc
        result = (payload or {}).get("result") or {}
        if pages is None:
            try:
                pages = int(result.get("pages") or 1)
            except (TypeError, ValueError):
                pages = 1
        rows = result.get("data") or []
        if not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            full = _normalize_secucode(row.get("SECUCODE") or row.get("SECURITY_CODE"))
            if not full or full in seen:
                continue
            seen.add(full)
            items.append(
                {
                    "code": full,
                    "name": str(row.get("SECURITY_NAME_ABBR") or "").strip(),
                    "market": full.split(".", 1)[1],
                }
            )
        if page >= pages:
            break
        page += 1
        if page > 500:
            break
    return items


def fetch_etf_universe(
    *,
    page_size: int = 100,
    session: Optional[requests.Session] = None,
    timeout: float = 20.0,
    persist: bool = True,
    path: Optional[Path] = None,
) -> List[Dict[str, str]]:
    """拉取完整 ETF 池。返回 [{code, name, market}, ...]，code 形如 510300.SH。"""
    sess = session or _session()
    source = _SOURCE_CLIST
    try:
        items = _fetch_via_clist(sess, page_size=page_size, timeout=timeout)
        if len(items) < 500:
            raise RuntimeError(f"clist_too_few:{len(items)}")
    except Exception:
        source = _SOURCE_DC
        items = _fetch_via_datacenter(sess, page_size=max(page_size, 100), timeout=timeout)
    items.sort(key=lambda x: x["code"])
    if persist:
        save_etf_universe(items, path=path, source=source)
    return items


def save_etf_universe(
    items: List[Dict[str, str]],
    *,
    path: Optional[Path] = None,
    source: str = _SOURCE_CLIST,
) -> Path:
    out = path or UNIVERSE_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "source": source,
        "fetched_at": datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        "count": len(items),
        "codes": [it["code"] for it in items],
        "items": items,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def load_etf_universe(
    *,
    path: Optional[Path] = None,
    refresh: bool = False,
) -> List[Dict[str, str]]:
    """读本地池；不存在、refresh=True、或 fetched_at 不是今天时重新拉取。"""
    if refresh:
        return fetch_etf_universe(persist=True, path=path)
    p = path or UNIVERSE_PATH
    if not p.is_file():
        return fetch_etf_universe(persist=True, path=path)
    data = json.loads(p.read_text(encoding="utf-8"))
    fetched = str(data.get("fetched_at") or "")[:10]
    today = datetime.now().strftime("%Y-%m-%d")
    if fetched and fetched < today:
        return fetch_etf_universe(persist=True, path=path)
    items = data.get("items")
    if isinstance(items, list) and items:
        out = []
        for it in items:
            if isinstance(it, dict) and it.get("code"):
                out.append(
                    {
                        "code": str(it["code"]),
                        "name": str(it.get("name") or ""),
                        "market": str(it.get("market") or ""),
                    }
                )
        if out:
            return out
    codes = data.get("codes") or []
    return [{"code": str(c), "name": "", "market": str(c).split(".")[-1]} for c in codes if c]


def etf_codes(*, refresh: bool = False) -> List[str]:
    return [it["code"] for it in load_etf_universe(refresh=refresh)]


if __name__ == "__main__":
    rows = fetch_etf_universe()
    print(f"ETF universe {len(rows)} -> {UNIVERSE_PATH}")
    for it in rows[:5]:
        print(it)
