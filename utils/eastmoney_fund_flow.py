# -*- coding: utf-8 -*-
"""东方财富个股资金流向排行（push2 JSON，全量分页）。

对应网页：https://data.eastmoney.com/zjlx/detail.html
优先于 Selenium：快。盘后默认先按主力净流入门槛早停落盘子集，再继续翻页尝试全量；全量失败保留子集。

网络：先直连；若被对端掐断，再试本机代理（EM_FUND_FLOW_PROXY / EM_HIST_PROXY /
环境变量 HTTPS_PROXY，以及常见本地端口 7078/7890）。
盘后默认仅东财 push2；仅显式 allow_ths=True 时才允许同花顺兜底。
若 push2 持续 RemoteDisconnected（出口/IPv4 被对端掐），请临时加 --allow-ths。
请求顺序：系统双栈优先，失败再 prefer_ipv4（办公网 IPv6 黑洞场景）。
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

from utils.http_ipv4 import as_timeout, prefer_ipv4

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


def _do_get(
    session: requests.Session,
    host: str,
    params: Dict[str, Any],
    proxies: Optional[Dict[str, str]],
    *,
    force_ipv4: bool,
):
    """单次 GET；force_ipv4=False 走系统双栈（东财 IPv4 当前常被掐）。"""
    def _once():
        r = session.get(
            host,
            params=params,
            headers=_HEADERS,
            timeout=as_timeout((3.0, 25.0)),
            proxies=proxies or {},
        )
        r.raise_for_status()
        return r.json()

    if force_ipv4:
        with prefer_ipv4():
            return _once()
    return _once()


def _get_json(
    session: requests.Session,
    params: Dict[str, Any],
    *,
    hosts: Sequence[str] = _HOSTS,
    rounds: int = 3,
) -> dict:
    """代理 × 多 host × 双栈优先再 IPv4；记住可用代理，全失败退避重试。

    2026-10 起东财 push2 的 IPv4 常 RemoteDisconnected，而 IPv6/双栈正常；
    故默认先不强制 IPv4，失败后再用 prefer_ipv4 兜底（办公网 IPv6 黑洞场景）。
    """
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
                # 先双栈，再强制 IPv4
                for force_v4 in (False, True):
                    try:
                        data = _do_get(
                            session, host, params, proxies, force_ipv4=force_v4
                        )
                        if isinstance(data, dict) and data.get("data") is not None:
                            _WORKING_PROXIES = proxies
                            _WORKING_PROXIES_RESOLVED = True
                            return data
                        last_err = RuntimeError(f"empty payload from {host}")
                    except Exception as e:
                        last_err = e
                        msg = str(e).lower()
                        if proxies and (
                            "10061" in msg
                            or "refused" in msg
                            or "unable to connect to proxy" in msg
                        ):
                            _DEAD_PROXIES.add(_proxy_key(proxies))
                            # 代理挂了，换下一个代理（跳出 force_v4 与 host 循环由外层 break）
                            break
                        # 双栈失败再试 IPv4；IPv4 也失败再短睡
                        if force_v4:
                            time.sleep(0.25 * attempt + random.random() * 0.25)
                else:
                    continue
                # proxies dead → break host loop
                break
        if round_i + 1 < rounds:
            time.sleep(1.5 * (round_i + 1) + random.random())

    raise RuntimeError(f"东方财富资金流接口失败: {last_err}")

def _row_inflow_yuan(r: dict) -> float:
    try:
        return float(r.get("f62") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _filter_rows_min_inflow(rows: List[dict], min_inflow_wan: float) -> List[dict]:
    thr = float(min_inflow_wan) * 1e4
    out: List[dict] = []
    for r in rows:
        if _row_inflow_yuan(r) + 1e-9 >= thr:
            out.append(r)
    return out


def fetch_individual_fund_flow_rows(
    *,
    page_size: int = 100,
    pause_s: float = 0.08,
    min_inflow_wan: Optional[float] = None,
    then_full: bool = False,
    on_min_subset: Optional[Any] = None,
) -> Tuple[List[dict], dict]:
    """
    拉取「今日」个股资金流排行（按 f62 主力净流入降序）。

    - min_inflow_wan 为空/<=0：翻完全市场（旧行为）。
    - min_inflow_wan >0：先翻到「整页都 < 门槛」得到子集；
      then_full=True 时继续往后补全市场；中途失败则返回已拿到的 >=门槛 子集。
    - on_min_subset(rows, meta)：子集刚齐全时回调（便于先落盘）。
    """
    session = _session()
    pz = max(20, min(int(page_size), 100))
    thr_wan = float(min_inflow_wan) if min_inflow_wan is not None else 0.0
    thr_yuan = thr_wan * 1e4
    want_min = thr_wan > 0
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
    min_rows: List[dict] = []
    min_done = False
    pages_ok = 0
    incomplete = False
    full_error = ""
    max_pages = max(1, (total + pz - 1) // pz) if total else 200

    def _extend_page(diff_obj: Any) -> Tuple[int, int]:
        """返回 (page_len, page_kept_ge_thr)。"""
        page_list: List[dict] = []
        if not diff_obj:
            return 0, 0
        if isinstance(diff_obj, dict):
            page_list = list(diff_obj.values())
        else:
            page_list = list(diff_obj)
        kept = 0
        for r in page_list:
            rows.append(r)
            if want_min and (not min_done):
                if _row_inflow_yuan(r) + 1e-9 >= thr_yuan:
                    min_rows.append(r)
                    kept += 1
            elif want_min and min_done:
                pass
            else:
                kept += 1
        return len(page_list), kept

    page_len, page_kept = _extend_page(data.get("diff"))
    pages_ok = 1 if page_len else 0

    def _maybe_finish_min(pn: int, page_len: int, page_kept: int) -> bool:
        nonlocal min_done
        if not want_min or min_done:
            return False
        # 整页都 < 门槛、空页、或本页已出现 < 门槛（子集已齐）
        if page_len == 0 or page_kept == 0 or page_kept < page_len:
            min_done = True
            meta_cb = {
                "source": "eastmoney_push2",
                "total": total,
                "fetched": len(min_rows),
                "pages": pn,
                "page_size": pz,
                "coverage": "min_inflow_subset",
                "min_inflow_wan": thr_wan,
                "stopped_early": True,
            }
            if callable(on_min_subset):
                try:
                    on_min_subset(list(min_rows), dict(meta_cb))
                except Exception as e:
                    # 回调失败不阻断后续全量
                    meta_cb["on_min_subset_error"] = f"{type(e).__name__}: {e}"
            return True
        return False

    if pages_ok:
        _maybe_finish_min(1, page_len, page_kept)

    # 只要还没拿到子集齐全，或需要继续全量，就继续翻页
    pn = 1
    while pn < max_pages:
        if want_min and min_done and not then_full:
            break
        if (not want_min) and total and pn >= max_pages:
            break
        if total and pn >= max_pages:
            break
        # 无 total 时：空页即停
        pn += 1
        params = dict(params)
        params["pn"] = str(pn)
        try:
            try:
                payload = _get_json(session, params, rounds=2)
            except Exception:
                global _WORKING_PROXIES_RESOLVED
                _WORKING_PROXIES_RESOLVED = False
                payload = _get_json(session, params, rounds=3)
        except Exception as e:
            incomplete = True
            full_error = f"{type(e).__name__}: {e}"
            break
        page_len, page_kept = _extend_page((payload.get("data") or {}).get("diff"))
        if page_len == 0:
            if want_min and not min_done:
                _maybe_finish_min(pn, 0, 0)
            break
        pages_ok = pn
        just_min = _maybe_finish_min(pn, page_len, page_kept)
        if pause_s > 0:
            time.sleep(pause_s)
        # 旧全量：按 total_pages 走完
        if (not want_min) and total and pn >= max_pages:
            break
        if want_min and min_done and then_full and total and pn >= max_pages:
            break
        if just_min and not then_full:
            break

    if want_min and not min_done and rows:
        # 翻完仍未穿越门槛：子集=全部已拉且均可能 >=门槛
        min_rows = _filter_rows_min_inflow(rows, thr_wan)
        min_done = True
        if callable(on_min_subset) and min_rows:
            try:
                on_min_subset(
                    list(min_rows),
                    {
                        "source": "eastmoney_push2",
                        "total": total,
                        "fetched": len(min_rows),
                        "pages": pages_ok,
                        "page_size": pz,
                        "coverage": "min_inflow_subset",
                        "min_inflow_wan": thr_wan,
                        "stopped_early": False,
                        "note": "exhausted_pages_without_cross",
                    },
                )
            except Exception:
                pass

    full_ok = (not incomplete) and (
        (total > 0 and len(rows) >= max(1, int(total * 0.95)))
        or ((not total) and pages_ok > 0 and not want_min)
        or (want_min and then_full and not incomplete and total > 0 and len(rows) >= max(1, int(total * 0.95)))
        or (want_min and then_full and not incomplete and (not total) and pages_ok >= max_pages)
    )
    # 更朴素：无中断且页数走到 max_pages（或空页自然结束且非 incomplete）
    if not incomplete and total > 0 and pages_ok >= max_pages:
        full_ok = True
    if not incomplete and total > 0 and len(rows) >= total:
        full_ok = True

    if want_min and then_full and full_ok:
        out_rows = rows
        coverage = "full"
    elif want_min:
        out_rows = list(min_rows) if min_rows else _filter_rows_min_inflow(rows, thr_wan)
        coverage = "min_inflow_subset"
    else:
        out_rows = rows
        coverage = "full" if full_ok else "partial"

    meta = {
        "source": "eastmoney_push2",
        "total": total,
        "fetched": len(out_rows),
        "fetched_all_pages": len(rows),
        "pages": pages_ok,
        "page_size": pz,
        "via_proxy": bool(_WORKING_PROXIES),
        "proxy": (_WORKING_PROXIES or {}).get("https")
        or (_WORKING_PROXIES or {}).get("http")
        or "",
        "coverage": coverage,
        "min_inflow_wan": thr_wan if want_min else 0.0,
        "min_subset_ready": bool(want_min and min_done),
        "incomplete": bool(incomplete),
        "full_ok": bool(full_ok),
        "full_error": full_error,
        "stopped_early": bool(want_min and coverage == "min_inflow_subset"),
    }
    return out_rows, meta


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
    min_inflow_wan: Optional[float] = None,
    then_full: bool = False,
    on_min_subset: Optional[Any] = None,
) -> Tuple[pd.DataFrame, dict]:
    rows, meta = fetch_individual_fund_flow_rows(
        page_size=page_size,
        pause_s=pause_s,
        min_inflow_wan=min_inflow_wan,
        then_full=then_full,
        on_min_subset=on_min_subset,
    )
    df = rows_to_flow_dataframe(rows)
    meta["dataframe_rows"] = len(df)
    return df, meta


def fetch_individual_fund_flow_df_resilient(
    *,
    page_size: int = 100,
    pause_s: float = 0.08,
    as_of: Optional[date] = None,
    allow_ths: bool = False,
    prefer_min_inflow_wan: float = 3000.0,
    then_full: bool = True,
    on_min_subset: Optional[Any] = None,
) -> Tuple[pd.DataFrame, dict]:
    """东财 push2：先保 >=prefer_min_inflow_wan 子集，再尝试全量；仅 allow_ths=True 时同花顺兜底。"""
    errors: List[str] = []
    min_wan = float(prefer_min_inflow_wan or 0.0)
    try:
        if min_wan > 0:
            df, meta = fetch_individual_fund_flow_df(
                page_size=page_size,
                pause_s=pause_s,
                min_inflow_wan=min_wan,
                then_full=bool(then_full),
                on_min_subset=on_min_subset,
            )
        else:
            df, meta = fetch_individual_fund_flow_df(
                page_size=page_size,
                pause_s=pause_s,
            )
        if df is not None and not df.empty:
            # 子集或全量只要非空即成功；全量不足时 coverage 已标 min_inflow_subset
            if meta.get("coverage") == "full" or meta.get("full_ok"):
                meta["coverage"] = "full"
                return df, meta
            if min_wan > 0 and (
                meta.get("min_subset_ready")
                or meta.get("coverage") == "min_inflow_subset"
                or len(df) > 0
            ):
                meta["coverage"] = "min_inflow_subset"
                return df, meta
            if len(df) >= 1000:
                meta["coverage"] = meta.get("coverage") or "full"
                return df, meta
        errors.append(
            f"eastmoney rows too few: {0 if df is None else len(df)} "
            f"coverage={None if not meta else meta.get('coverage')}"
        )
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
