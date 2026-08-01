# -*- coding: utf-8 -*-
"""选股引擎用：按日加载「净流入占流通%」排名，供规则 ctx['inflow_rank'] 使用。"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, Optional, Union

import pandas as pd

from utils.main_force_inflow_path import resolve_flow_csv_path

RANK_COL = "净流入占流通%"
DateLike = Union[date, datetime, str, None]


def _ymd(d: DateLike) -> Optional[str]:
    if d is None:
        d = date.today()
    if isinstance(d, datetime):
        d = d.date()
    if isinstance(d, date):
        return d.strftime("%Y%m%d")
    s = str(d).strip()
    if len(s) >= 10 and s[4] == "-":
        return s[0:4] + s[5:7] + s[8:10]
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    return None


def _norm_code(v: Any) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v).strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def _parse_pct(v: Any) -> Optional[float]:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("%", "").replace(",", "").replace("\xa0", "")
    if not s or s in ("--", "-", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _read_csv(path: str) -> pd.DataFrame:
    last_err: Exception | None = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return pd.read_csv(path, encoding=enc)
        except Exception as e:  # noqa: BLE001
            last_err = e
    raise RuntimeError(f"无法读取 {path}: {last_err}")


def _normalize_old_headers(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    work.columns = [str(c).strip() for c in work.columns]
    rename = {
        "今日\n涨跌幅": "今日涨跌幅",
        "今日主力净流入": "今日主力净流入-净额",
        "今日超大单净流入": "今日主力净流入-净占比",
        "今日大单净流入": "今日超大单净流入-净额",
        "今日中单净流入": "今日超大单净流入-净占比",
        "今日小单净流入": "今日大单净流入-净额",
        "净额": "今日大单净流入-净占比",
        "净占比": "今日中单净流入-净额",
        "净额.1": "今日中单净流入-净占比",
        "净占比.1": "今日小单净流入-净额",
        "净额.2": "今日小单净流入-净占比",
    }
    if "相关" in work.columns or "今日\n涨跌幅" in work.columns or "净额.2" in work.columns:
        work = work.rename(columns=rename)
        if "相关" in work.columns:
            work = work.drop(columns=["相关"])
    return work


def load_inflow_rank_map(
    as_of: DateLike,
    *,
    history_dir: str = "history_data",
    top_n: Optional[int] = None,
) -> Dict[str, Dict[str, Any]]:
    """返回 {code6: {rank, pct, name, raw_row...}}，按净流入占流通% 降序排名。

    若缺「净流入占流通%」但有「序号」，则按序号升序当作排名（存档旧表）。
    """
    ymd = _ymd(as_of)
    if not ymd:
        return {}
    path = resolve_flow_csv_path(ymd, history_dir)
    if not path:
        # 绝对 history_data（项目根下）
        root = Path(__file__).resolve().parents[1]
        path = resolve_flow_csv_path(ymd, str(root / "history_data"))
    if not path:
        return {}

    raw = _normalize_old_headers(_read_csv(path))
    code_col = "代码" if "代码" in raw.columns else None
    if code_col is None:
        for c in raw.columns:
            if "代码" in str(c):
                code_col = c
                break
    if code_col is None:
        return {}

    work = raw.copy()
    work["_code"] = work[code_col].map(_norm_code)
    work = work[work["_code"].astype(str).str.len() == 6]

    if RANK_COL in work.columns and work[RANK_COL].map(_parse_pct).notna().any():
        work["_pct"] = work[RANK_COL].map(_parse_pct)
        work = work.dropna(subset=["_pct"]).sort_values("_pct", ascending=False, kind="mergesort")
    elif "序号" in work.columns:
        work["序号"] = pd.to_numeric(work["序号"], errors="coerce")
        work = work.dropna(subset=["序号"]).sort_values("序号", ascending=True, kind="mergesort")
        work["_pct"] = work[RANK_COL].map(_parse_pct) if RANK_COL in work.columns else None
    else:
        return {}

    if top_n is not None:
        work = work.head(int(top_n))

    name_col = "名称" if "名称" in work.columns else None
    out: Dict[str, Dict[str, Any]] = {}
    for i, (_, row) in enumerate(work.iterrows(), start=1):
        code = str(row["_code"])
        pct = row["_pct"] if "_pct" in row.index else None
        try:
            pct_f = float(pct) if pct is not None and pct == pct else None
        except (TypeError, ValueError):
            pct_f = None
        out[code] = {
            "rank": i,
            "pct": pct_f,
            "name": str(row[name_col]) if name_col else "",
        }
    return out
