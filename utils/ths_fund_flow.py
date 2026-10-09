# -*- coding: utf-8 -*-
"""同花顺个股资金流（全市场），作东财 push2 不可用时的兜底。

页面：https://data.10jqka.com.cn/funds/ggzjl/
字段少于东财（无超大/大/中/小单拆分），但「净额」可映射为主力净流入净额，
行数可达全市场级别（~5200），满足盘后落盘与 JSONL 导出门槛。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Dict, Optional, Tuple

import pandas as pd

from utils.main_force_inflow_rank import load_float_market_cap_yuan_map, yuan_to_display


def _zfill_code(raw: Any) -> str:
    s = "".join(ch for ch in str(raw or "") if ch.isdigit())
    if not s:
        return ""
    return (s.zfill(6) if len(s) < 6 else s)[-6:]


def _fmt_pct(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    if not s or s in ("--", "-", "nan"):
        return ""
    if s.endswith("%"):
        return s
    try:
        return f"{float(s):.2f}%"
    except (TypeError, ValueError):
        return s


def _fmt_price(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    try:
        return f"{float(str(v).strip().replace(',', '')):.2f}"
    except (TypeError, ValueError):
        return str(v).strip()


def ths_raw_to_flow_dataframe(
    raw: pd.DataFrame,
    *,
    as_of: Optional[date] = None,
) -> pd.DataFrame:
    """把同花顺原始表映射为与东财 CSV 兼容的中文列。"""
    if raw is None or raw.empty:
        return pd.DataFrame()

    colmap = {str(c).strip(): c for c in raw.columns}

    def _col(*names: str) -> Optional[Any]:
        for n in names:
            if n in colmap:
                return colmap[n]
        for n in names:
            for k, orig in colmap.items():
                if n in k:
                    return orig
        return None

    code_c = _col("股票代码", "代码")
    name_c = _col("股票简称", "名称", "简称")
    price_c = _col("最新价")
    pct_c = _col("涨跌幅")
    net_c = _col("净额", "主力净流入")
    if not code_c or not net_c:
        raise RuntimeError(f"同花顺资金流缺必要列: {list(raw.columns)}")

    # 优先用近几日东财 CSV 的流通市值，便于按净流入/流通占比排序
    cap_map = load_float_market_cap_yuan_map(as_of or date.today())

    out_rows = []
    for i, (_, r) in enumerate(raw.iterrows(), start=1):
        code = _zfill_code(r.get(code_c))
        if not code.isdigit():
            continue
        name = str(r.get(name_c) or "").strip() if name_c else ""
        net_raw = r.get(net_c)
        net_disp = "" if net_raw is None or (isinstance(net_raw, float) and pd.isna(net_raw)) else str(net_raw).strip()
        if not net_disp or net_disp in ("--", "-"):
            continue
        cap_yuan = cap_map.get(code)
        cap_disp = yuan_to_display(cap_yuan) if cap_yuan and cap_yuan > 0 else ""
        out_rows.append(
            {
                "序号": i,
                "代码": code,
                "名称": name,
                "最新价": _fmt_price(r.get(price_c)) if price_c else "",
                "今日涨跌幅": _fmt_pct(r.get(pct_c)) if pct_c else "",
                "今日主力净流入-净额": net_disp,
                "流通市值": cap_disp,
                "净流入占流通%": "",
                "今日主力净流入-净占比": "",
                "今日超大单净流入-净额": "",
                "今日超大单净流入-净占比": "",
                "今日大单净流入-净额": "",
                "今日大单净流入-净占比": "",
                "今日中单净流入-净额": "",
                "今日中单净流入-净占比": "",
                "今日小单净流入-净额": "",
                "今日小单净流入-净占比": "",
            }
        )

    if not out_rows:
        return pd.DataFrame()
    return pd.DataFrame(out_rows)


def fetch_ths_individual_fund_flow_df(
    *,
    as_of: Optional[date] = None,
) -> Tuple[pd.DataFrame, dict]:
    """拉取同花顺「即时」个股资金流全表。"""
    import akshare as ak

    raw = ak.stock_fund_flow_individual(symbol="即时")
    if raw is None or not isinstance(raw, pd.DataFrame) or raw.empty:
        raise RuntimeError("同花顺个股资金流返回空表")
    df = ths_raw_to_flow_dataframe(raw, as_of=as_of)
    meta = {
        "source": "ths",
        "total": len(raw),
        "fetched": len(raw),
        "dataframe_rows": len(df),
        "pages": None,
        "via_proxy": False,
        "proxy": "",
    }
    if len(df) < 1000:
        raise RuntimeError(f"同花顺资金流行数过少: {len(df)}")
    return df, meta
