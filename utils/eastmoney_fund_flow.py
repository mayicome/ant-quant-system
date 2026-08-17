# -*- coding: utf-8 -*-
"""东方财富个股资金流向排行（push2 JSON，全量分页）。

对应网页：https://data.eastmoney.com/zjlx/detail.html
优先于 Selenium：快、可拿全市场，适合盘后落盘。
"""
from __future__ import annotations

import random
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

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
)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Referer": "https://data.eastmoney.com/zjlx/detail.html",
}


def _session() -> requests.Session:
    s = requests.Session()
    s.trust_env = False  # 避开系统坏代理
    return s


def _get_json(
    session: requests.Session,
    params: Dict[str, Any],
    *,
    hosts: Sequence[str] = _HOSTS,
    retries: int = 5,
) -> dict:
    last_err: Optional[Exception] = None
    for attempt in range(max(1, retries)):
        host = hosts[attempt % len(hosts)]
        try:
            r = session.get(host, params=params, headers=_HEADERS, timeout=30)
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict) and data.get("data") is not None:
                return data
            last_err = RuntimeError(f"empty payload from {host}")
        except Exception as e:
            last_err = e
            time.sleep(0.5 * (attempt + 1) + random.random() * 0.2)
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
        payload = _get_json(session, params)
        _extend((payload.get("data") or {}).get("diff"))
        if pause_s > 0:
            time.sleep(pause_s)

    meta = {
        "total": total,
        "fetched": len(rows),
        "pages": total_pages,
        "page_size": pz,
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
