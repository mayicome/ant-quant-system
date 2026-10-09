# -*- coding: utf-8 -*-
"""东方财富个股资金流向排行（push2 JSON，全量分页）。

对应网页：https://data.eastmoney.com/zjlx/detail.html
优先于 Selenium：快、可拿全市场，适合盘后落盘。

网络：先直连；若被对端掐断，再试本机代理（EM_FUND_FLOW_PROXY / EM_HIST_PROXY /
环境变量 HTTPS_PROXY，以及常见本地端口 7078/7890）。
push2 全挂时由 fetch_individual_fund_flow_df_resilient 改走同花顺兜底。
"""
from __future__ import annotations

import os
import random
import socket
import time
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

import pandas as pd
import requests

from utils.main_force_inflow_rank import yuan_to_display

# akshare stock_individual_fund_flow_rank 同源筛选
_FS = (
    "m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,"
    "m:1+t:2+f:!2,m:1+t:23+f:!2,m:0+t:7+f:!2,m:1+t:3+f:!2"
)
# f15=最高 f16=最低 f17=今开 f18=昨收；供盘中判断「当天是否涨停过」
_FIELDS = (
    "f12,f14,f2,f3,f15,f16,f17,f18,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f20,f21"
)
_UT = "b2884a393a59ad64002292a3e90d46a5"

_HOSTS = (
    "https://push2delay.eastmoney.com/api/qt/clist/get",
    "https://82.push2.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
    "https://7.push2.eastmoney.com/api/qt/clist/get",
    "https://55.push2.eastmoney.com/api/qt/clist/get",
    "https://94.push2.eastmoney.com/api/qt/clist/get",
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/zjlx/detail.html",
    "Origin": "https://data.eastmoney.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}

# 进程内记住本轮可用的代理（None=直连）
_WORKING_PROXIES: Optional[Dict[str, str]] = None
_WORKING_PROXIES_RESOLVED = False
_DEAD_PROXIES: set = set()


def _proxy_candidates() -> List[Optional[Dict[str, str]]]:
    """直连优先，再试显式/环境/常见本地代理。"""
    out: List[Optional[Dict[str, str]]] = [None]
    seen = {""}

    def _add(url: str) -> None:
        u = (url or "").strip()
        if not u or u in seen or u in _DEAD_PROXIES:
            return
        seen.add(u)
        out.append({"http": u, "https": u})

    for key in (
        "EM_FUND_FLOW_PROXY",
        "EM_HIST_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
        "ALL_PROXY",
    ):
        _add(os.environ.get(key, ""))
    for port in (7078, 7890, 7897, 10809, 10808, 1080):
        _add(f"http://127.0.0.1:{port}")
    return out


def _proxy_key(proxies: Optional[Dict[str, str]]) -> str:
    if not proxies:
        return ""
    return str(proxies.get("https") or proxies.get("http") or "")


def _proxy_tcp_alive(proxies: Optional[Dict[str, str]], *, timeout: float = 0.6) -> bool:
    """本地代理端口不通则直接跳过，避免拖死盘后批跑。"""
    if not proxies:
        return True
    url = _proxy_key(proxies)
    if not url:
        return True
    try:
        u = urlparse(url)
        host = u.hostname or "127.0.0.1"
        port = int(u.port or (443 if u.scheme == "https" else 80))
    except Exception:
        return False
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        _DEAD_PROXIES.add(url)
        return False


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False  # 避开系统坏代理；需要时代码显式传入 proxies
    return s


def _get_json(
    session: requests.Session,
    params: Dict[str, Any],
    *,
    hosts: Sequence[str] = _HOSTS,
    rounds: int = 3,
) -> dict:
    """多轮 × 多 host × 可用代理；单次失败短暂退避，整轮失败再拉长等待。"""
    global _WORKING_PROXIES, _WORKING_PROXIES_RESOLVED
    last_err: Optional[Exception] = None

    for round_i in range(max(1, rounds)):
        proxy_list = _proxy_candidates()
        if _WORKING_PROXIES_RESOLVED:
            preferred = _WORKING_PROXIES
            rest = [p for p in proxy_list if p != preferred]
            proxy_list = [preferred] + rest

        attempt = 0
        for proxies in proxy_list:
            if not _proxy_tcp_alive(proxies):
                continue
            for host in hosts:
                attempt += 1
                try:
                    r = session.get(
                        host,
                        params=params,
                        headers=_HEADERS,
                        timeout=25,
                        proxies=proxies or {},
                    )
                    r.raise_for_status()
                    data = r.json()
                    if isinstance(data, dict) and data.get("data") is not None:
                        _WORKING_PROXIES = proxies
                        _WORKING_PROXIES_RESOLVED = True
                        return data
                    last_err = RuntimeError(f"empty payload from {host}")
                except Exception as e:
                    last_err = e
                    msg = str(e).lower()
                    # 代理拒绝/不存在：记入死名单，本轮不再试
                    if proxies and (
                        "10061" in msg
                        or "refused" in msg
                        or "unable to connect to proxy" in msg
                    ):
                        _DEAD_PROXIES.add(_proxy_key(proxies))
                        break
                    time.sleep(0.25 * attempt + random.random() * 0.25)
        if round_i + 1 < rounds:
            time.sleep(1.5 * (round_i + 1) + random.random())

    raise RuntimeError(f"东方财富资金流接口失败: {last_err}")


def fetch_individual_fund_flow_rows(
    *,
    page_size: int = 100,
    pause_s: float = 0.08,
) -> Tuple[List[dict], dict]:
    """
    拉取全市场「今日」个股资金流排行。

    返回 (raw_rows, meta)；raw_rows 为接口 diff 元素列表。
    """
    session = _session()
    pz = max(20, min(int(page_size), 100))
    params = {
        "fid": "f62",
        "po": "1",
        "pz": str(pz),
        "pn": "1",
        "np": "1",
        "fltt": "2",
        "invt": "2",
        "ut": _UT,
        "fs": _FS,
        "fields": _FIELDS,
    }
    first = _get_json(session, params)
    data = first.get("data") or {}
    total = int(data.get("total") or 0)
    rows: List[dict] = []

    def _extend(diff_obj: Any) -> None:
        if not diff_obj:
            return
        if isinstance(diff_obj, dict):
            rows.extend(list(diff_obj.values()))
        else:
            rows.extend(list(diff_obj))

    _extend(data.get("diff"))
    total_pages = max(1, (total + pz - 1) // pz) if total else 1

    for pn in range(2, total_pages + 1):
        params = dict(params)
        params["pn"] = str(pn)
        # 翻页失败时重置「可用代理」记忆，再试一轮（push2 中途掐线常见）
        try:
            payload = _get_json(session, params, rounds=2)
        except Exception:
            global _WORKING_PROXIES_RESOLVED
            _WORKING_PROXIES_RESOLVED = False
            payload = _get_json(session, params, rounds=3)
        _extend((payload.get("data") or {}).get("diff"))
        if pause_s > 0:
            time.sleep(pause_s)

    meta = {
        "source": "eastmoney_push2",
        "total": total,
        "fetched": len(rows),
        "pages": total_pages,
        "page_size": pz,
        "via_proxy": bool(_WORKING_PROXIES),
        "proxy": (_WORKING_PROXIES or {}).get("https")
        or (_WORKING_PROXIES or {}).get("http")
        or "",
    }
    return rows, meta


def _fmt_pct(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    try:
        return f"{float(v):.2f}%"
    except (TypeError, ValueError):
        return ""


def _fmt_price(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return ""


def rows_to_flow_dataframe(rows: List[dict]) -> pd.DataFrame:
    """转成与历史 CSV 兼容的中文列（金额用亿/万展示串）。"""
    out_rows = []
    for i, r in enumerate(rows, start=1):
        code = str(r.get("f12") or "").zfill(6)[-6:]
        if not code.isdigit():
            continue
        name = str(r.get("f14") or "").strip()
        try:
            main = float(r.get("f62") or 0.0)
        except (TypeError, ValueError):
            main = 0.0
        try:
            float_cap = float(r.get("f21") or 0.0)
        except (TypeError, ValueError):
            float_cap = 0.0
        if float_cap <= 0:
            try:
                float_cap = float(r.get("f20") or 0.0)
            except (TypeError, ValueError):
                float_cap = 0.0

        ratio_pct = ""
        if float_cap > 0:
            ratio_pct = round(main / float_cap * 100.0, 4)

        def _amt(key: str) -> str:
            try:
                return yuan_to_display(float(r.get(key) or 0.0))
            except (TypeError, ValueError):
                return ""

        out_rows.append(
            {
                "序号": i,
                "代码": code,
                "名称": name,
                "最新价": _fmt_price(r.get("f2")),
                "今日涨跌幅": _fmt_pct(r.get("f3")),
                "今日主力净流入-净额": yuan_to_display(main),
                "流通市值": yuan_to_display(float_cap) if float_cap > 0 else "",
                "净流入占流通%": ratio_pct,
                "今日主力净流入-净占比": _fmt_pct(r.get("f184")),
                "今日超大单净流入-净额": _amt("f66"),
                "今日超大单净流入-净占比": _fmt_pct(r.get("f69")),
                "今日大单净流入-净额": _amt("f72"),
                "今日大单净流入-净占比": _fmt_pct(r.get("f75")),
                "今日中单净流入-净额": _amt("f78"),
                "今日中单净流入-净占比": _fmt_pct(r.get("f81")),
                "今日小单净流入-净额": _amt("f84"),
                "今日小单净流入-净占比": _fmt_pct(r.get("f87")),
                "_ratio": (main / float_cap) if float_cap > 0 else float("-inf"),
            }
        )

    if not out_rows:
        return pd.DataFrame()

    df = pd.DataFrame(out_rows)
    df = df.sort_values("_ratio", ascending=False, kind="mergesort").reset_index(drop=True)
    df["序号"] = range(1, len(df) + 1)
    df = df.drop(columns=["_ratio"], errors="ignore")
    return df


def fetch_individual_fund_flow_df(
    *,
    page_size: int = 100,
    pause_s: float = 0.08,
) -> Tuple[pd.DataFrame, dict]:
    rows, meta = fetch_individual_fund_flow_rows(page_size=page_size, pause_s=pause_s)
    df = rows_to_flow_dataframe(rows)
    meta["dataframe_rows"] = len(df)
    return df, meta


def fetch_individual_fund_flow_df_resilient(
    *,
    page_size: int = 100,
    pause_s: float = 0.08,
    as_of: Optional[date] = None,
    allow_ths: bool = True,
) -> Tuple[pd.DataFrame, dict]:
    """东财 push2 优先；失败则同花顺全市场兜底。"""
    errors: List[str] = []
    try:
        df, meta = fetch_individual_fund_flow_df(page_size=page_size, pause_s=pause_s)
        if df is not None and not df.empty and len(df) >= 1000:
            return df, meta
        errors.append(f"eastmoney rows too few: {0 if df is None else len(df)}")
    except Exception as e:
        errors.append(f"eastmoney: {type(e).__name__}: {e}")

    if allow_ths:
        try:
            from utils.ths_fund_flow import fetch_ths_individual_fund_flow_df

            df, meta = fetch_ths_individual_fund_flow_df(as_of=as_of)
            meta["fallback_errors"] = errors
            return df, meta
        except Exception as e:
            errors.append(f"ths: {type(e).__name__}: {e}")

    raise RuntimeError("个股资金流全部数据源失败: " + " | ".join(errors))
