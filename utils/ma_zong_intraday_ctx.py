# -*- coding: utf-8 -*-
"""马总选股逻辑2：盘中涨幅 / 主力净流入 / 板块涨幅榜快照。

数据源：东财 push2（与 utils.eastmoney_fund_flow、tools.snapshot_eastmoney_board_rank 同源）。
盘中（选股日=今天）拉实时；历史日回退本地 CSV。
同一进程内按 TTL 缓存，避免选股引擎对每只股票重复拉全市场。
"""
from __future__ import annotations

import os
import time
from datetime import date, datetime
from typing import Any, Dict, Optional, Tuple, Union

import pandas as pd

DateLike = Union[date, datetime, str, None]

_CACHE: Dict[str, Any] = {"key": "", "ts": 0.0, "bundle": None}
_DEFAULT_TTL_SEC = 90.0


def _as_date(d: DateLike) -> Optional[date]:
    if d is None:
        return date.today()
    if isinstance(d, datetime):
        return d.date()
    if isinstance(d, date):
        return d
    s = str(d).strip()
    if len(s) >= 10 and s[4] == "-":
        try:
            return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
        except ValueError:
            return None
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        try:
            return date(int(digits[0:4]), int(digits[4:6]), int(digits[6:8]))
        except ValueError:
            return None
    return None


def _ymd(d: DateLike) -> str:
    dd = _as_date(d)
    return dd.strftime("%Y%m%d") if dd else ""


def _code6(v: Any) -> str:
    s = str(v or "").strip()
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
    s = str(v).strip().replace("%", "").replace(",", "")
    if not s or s in ("--", "-", "nan", "None"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _empty_bundle(as_of: date, *, mode: str) -> Dict[str, Any]:
    return {
        "as_of": as_of,
        "mode": mode,  # live | file
        "fetched_at": "",
        "quotes": {},  # code6 -> {pct, price, inflow_wan, name, float_mv_yi}
        "ind_rank": {},
        "con_rank": {},
        "ind_chg": {},  # name -> 涨跌幅%
        "con_chg": {},
        "ind_flow_ratio": {},  # name -> 今日主力净流入-净占比%
        "con_flow_ratio": {},
        "error": "",
        "board_err": "",
        "quote_err": "",
    }


def _df_to_rank_chg(df: pd.DataFrame) -> Tuple[Dict[str, int], Dict[str, float]]:
    rank: Dict[str, int] = {}
    chg: Dict[str, float] = {}
    if df is None or df.empty:
        return rank, chg
    for _, row in df.iterrows():
        name = str(row.get("板块名称") or "").strip()
        if not name or name in rank:
            continue
        try:
            rk = int(row.get("排名"))
        except (TypeError, ValueError):
            continue
        if rk < 1:
            continue
        rank[name] = rk
        pct = _parse_pct(row.get("涨跌幅"))
        if pct is None:
            pct = _parse_pct(row.get("今日涨跌幅"))
        if pct is not None:
            chg[name] = float(pct)
    return rank, chg


def _load_board_ranks_live() -> Tuple[
    Dict[str, int], Dict[str, int], Dict[str, float], Dict[str, float], str
]:
    try:
        from tools.snapshot_eastmoney_board_rank import fetch_board_rank_direct
    except Exception:
        # 允许从项目根以模块路径导入
        import sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        from tools.snapshot_eastmoney_board_rank import fetch_board_rank_direct

    err = ""
    ind: Dict[str, int] = {}
    con: Dict[str, int] = {}
    ind_chg: Dict[str, float] = {}
    con_chg: Dict[str, float] = {}
    try:
        df_i = fetch_board_rank_direct("industry")
        ind, ind_chg = _df_to_rank_chg(df_i)
    except Exception as e:
        err = "行业榜失败:%s" % e
    try:
        df_c = fetch_board_rank_direct("concept")
        con, con_chg = _df_to_rank_chg(df_c)
    except Exception as e:
        msg = "概念榜失败:%s" % e
        err = ("%s; %s" % (err, msg)).strip("; ")
    if not ind and not con and not err:
        err = "板块榜为空"
    return ind, con, ind_chg, con_chg, err


def _load_board_ranks_file(
    as_of: date,
) -> Tuple[Dict[str, int], Dict[str, int], Dict[str, float], Dict[str, float], str]:
    from utils.eastmoney_board_rank_ctx import board_rank_csv_paths, _load_rank_chg_maps

    paths = board_rank_csv_paths(as_of)
    ind, ind_chg = _load_rank_chg_maps(paths.get("industry") or "")
    con, con_chg = _load_rank_chg_maps(paths.get("concept") or "")
    err = ""
    if not ind and not con:
        err = "无本地行业/概念涨幅榜CSV"
    return ind, con, ind_chg, con_chg, err


def _df_to_flow_ratio(df: pd.DataFrame) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if df is None or df.empty:
        return out
    for _, row in df.iterrows():
        name = str(row.get("名称") or row.get("板块名称") or "").strip()
        if not name or name in out:
            continue
        pct = _parse_pct(row.get("今日主力净流入-净占比"))
        if pct is not None:
            out[name] = float(pct)
    return out


def _load_board_flow_ratio_live() -> Tuple[Dict[str, float], Dict[str, float], str]:
    try:
        from tools.snapshot_eastmoney_board_rank import fetch_fund_flow_direct
    except Exception:
        import sys
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if root not in sys.path:
            sys.path.insert(0, root)
        from tools.snapshot_eastmoney_board_rank import fetch_fund_flow_direct

    err = ""
    ind: Dict[str, float] = {}
    con: Dict[str, float] = {}
    try:
        ind = _df_to_flow_ratio(fetch_fund_flow_direct("行业资金流"))
    except Exception as e:
        err = "行业资金流失败:%s" % e
    try:
        con = _df_to_flow_ratio(fetch_fund_flow_direct("概念资金流"))
    except Exception as e:
        msg = "概念资金流失败:%s" % e
        err = ("%s; %s" % (err, msg)).strip("; ")
    return ind, con, err


def _load_board_flow_ratio_file(as_of: date) -> Tuple[Dict[str, float], Dict[str, float], str]:
    from utils.eastmoney_board_rank_ctx import (
        board_fund_flow_csv_paths,
        _load_fund_flow_net_ratio_map,
    )

    paths = board_fund_flow_csv_paths(as_of)
    ind = _load_fund_flow_net_ratio_map(paths.get("industry") or "")
    con = _load_fund_flow_net_ratio_map(paths.get("concept") or "")
    err = ""
    if not ind and not con:
        err = "无本地行业/概念资金流CSV"
    return ind, con, err


def _load_quotes_live() -> Tuple[Dict[str, Dict[str, Any]], str]:
    from utils.eastmoney_fund_flow import fetch_individual_fund_flow_rows
    from utils.main_force_inflow_rank import parse_inflow_to_yuan, yuan_to_display

    try:
        rows, meta = fetch_individual_fund_flow_rows()
    except Exception as e:
        return {}, "东财资金流拉取失败:%s" % e

    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        c6 = _code6(r.get("f12"))
        if not c6:
            continue
        pct = _parse_pct(r.get("f3"))
        try:
            price = float(r.get("f2")) if r.get("f2") not in (None, "") else None
        except (TypeError, ValueError):
            price = None

        def _fnum(key: str) -> Optional[float]:
            try:
                v = r.get(key)
                if v in (None, ""):
                    return None
                return float(v)
            except (TypeError, ValueError):
                return None

        high = _fnum("f15")
        low = _fnum("f16")
        open_px = _fnum("f17")
        pre_close = _fnum("f18")
        try:
            main_yuan = float(r.get("f62") or 0.0)
        except (TypeError, ValueError):
            main_yuan = 0.0
        try:
            float_cap = float(r.get("f21") or 0.0)
        except (TypeError, ValueError):
            float_cap = 0.0
        if float_cap <= 0:
            try:
                float_cap = float(r.get("f20") or 0.0)
            except (TypeError, ValueError):
                float_cap = 0.0
        out[c6] = {
            "pct": pct,
            "price": price,
            "high": high,
            "low": low,
            "open": open_px,
            "pre_close": pre_close,
            "inflow_wan": main_yuan / 1e4,
            "inflow_ratio": _parse_pct(r.get("f184")),
            "inflow_pct_of_float": (
                round(main_yuan / float_cap * 100.0, 4) if float_cap > 0 else None
            ),
            "inflow_display": yuan_to_display(main_yuan),
            "name": str(r.get("f14") or "").strip(),
            "float_mv_yi": (float_cap / 1e8) if float_cap > 0 else None,
        }
    if not out:
        return {}, "东财资金流为空(meta=%s)" % meta
    return out, ""


def _load_quotes_file(as_of: date) -> Tuple[Dict[str, Dict[str, Any]], str]:
    from utils.main_force_inflow_path import resolve_flow_csv_path
    from utils.main_force_inflow_rank import parse_inflow_to_yuan

    ymd = as_of.strftime("%Y%m%d")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = resolve_flow_csv_path(ymd, os.path.join(root, "history_data"))
    if not path:
        path = resolve_flow_csv_path(ymd, "history_data")
    if not path:
        return {}, "无主力净流入CSV"

    raw = None
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            raw = pd.read_csv(path, encoding=enc)
            break
        except Exception:
            continue
    if raw is None or raw.empty:
        return {}, "主力净流入CSV读失败"

    raw.columns = [str(c).strip() for c in raw.columns]
    code_col = "代码" if "代码" in raw.columns else None
    if code_col is None:
        for c in raw.columns:
            if "代码" in str(c):
                code_col = c
                break
    inflow_col = None
    for c in raw.columns:
        if "主力净流入" in str(c) and "净额" in str(c):
            inflow_col = c
            break
    if inflow_col is None:
        for c in raw.columns:
            if "主力净流入" in str(c):
                inflow_col = c
                break
    ratio_col = None
    if "今日主力净流入-净占比" in raw.columns:
        ratio_col = "今日主力净流入-净占比"
    else:
        for c in raw.columns:
            cs = str(c)
            if "主力净流入" in cs and "占比" in cs:
                ratio_col = c
                break
    pct_float_col = None
    if "净流入占流通%" in raw.columns:
        pct_float_col = "净流入占流通%"
    else:
        for c in raw.columns:
            if "净流入占流通" in str(c):
                pct_float_col = c
                break
    mv_col = "流通市值" if "流通市值" in raw.columns else None
    pct_col = "今日涨跌幅" if "今日涨跌幅" in raw.columns else None
    price_col = "最新价" if "最新价" in raw.columns else None
    name_col = "名称" if "名称" in raw.columns else None
    if not code_col or not inflow_col:
        return {}, "主力净流入CSV缺代码/净额列"

    out: Dict[str, Dict[str, Any]] = {}
    for _, row in raw.iterrows():
        c6 = _code6(row.get(code_col))
        if not c6:
            continue
        yuan = parse_inflow_to_yuan(row.get(inflow_col))
        if yuan is None:
            continue
        price = None
        if price_col is not None:
            try:
                pv = row.get(price_col)
                if pv not in (None, "") and str(pv).strip() not in ("--", "-", "nan"):
                    price = float(pv)
            except (TypeError, ValueError):
                price = None
        ratio = _parse_pct(row.get(ratio_col)) if ratio_col else None
        pct_float = _parse_pct(row.get(pct_float_col)) if pct_float_col else None
        float_mv_yi = None
        if mv_col is not None:
            mv_yuan = parse_inflow_to_yuan(row.get(mv_col))
            if mv_yuan and float(mv_yuan) > 0:
                float_mv_yi = float(mv_yuan) / 1e8
                if pct_float is None:
                    pct_float = float(yuan) / float(mv_yuan) * 100.0
        out[c6] = {
            "pct": _parse_pct(row.get(pct_col)) if pct_col else None,
            "price": price,
            "inflow_wan": float(yuan) / 1e4,
            "inflow_ratio": ratio,
            "inflow_pct_of_float": (
                None if pct_float is None else round(float(pct_float), 4)
            ),
            "inflow_display": str(row.get(inflow_col) or ""),
            "name": str(row.get(name_col) or "").strip() if name_col else "",
            "float_mv_yi": float_mv_yi,
        }
    if not out:
        return {}, "主力净流入CSV无有效行"
    return out, ""


def load_ma_zong_intraday_bundle(
    as_of: DateLike = None,
    *,
    force_live: Optional[bool] = None,
    ttl_sec: float = _DEFAULT_TTL_SEC,
    refresh: bool = False,
) -> Dict[str, Any]:
    """返回盘中/落地快照 bundle。

    force_live:
      None → 选股日=今天则 live，否则 file
      True/False → 强制
    """
    as_d = _as_date(as_of) or date.today()
    today = date.today()
    use_live = bool(force_live) if force_live is not None else (as_d == today)
    key = "%s:%s" % (as_d.isoformat(), "live" if use_live else "file")
    now = time.time()
    if (
        not refresh
        and _CACHE.get("key") == key
        and isinstance(_CACHE.get("bundle"), dict)
        and (now - float(_CACHE.get("ts") or 0.0)) < float(ttl_sec)
    ):
        return _CACHE["bundle"]

    bundle = _empty_bundle(as_d, mode="live" if use_live else "file")
    bundle["fetched_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if use_live:
        quotes, qerr = _load_quotes_live()
        ind, con, ind_chg, con_chg, berr = _load_board_ranks_live()
        ind_fr, con_fr, ferr = _load_board_flow_ratio_live()
        if ferr and not ind_fr and not con_fr:
            # live 资金流失败时回退当日本地 CSV
            ind_fr2, con_fr2, ferr2 = _load_board_flow_ratio_file(as_d)
            if ind_fr2 or con_fr2:
                ind_fr, con_fr = ind_fr2, con_fr2
                ferr = ""
            else:
                ferr = ferr2 or ferr
    else:
        quotes, qerr = _load_quotes_file(as_d)
        ind, con, ind_chg, con_chg, berr = _load_board_ranks_file(as_d)
        ind_fr, con_fr, ferr = _load_board_flow_ratio_file(as_d)

    bundle["quotes"] = quotes
    bundle["ind_rank"] = ind
    bundle["con_rank"] = con
    bundle["ind_chg"] = ind_chg
    bundle["con_chg"] = con_chg
    bundle["ind_flow_ratio"] = ind_fr
    bundle["con_flow_ratio"] = con_fr
    bundle["quote_err"] = qerr
    board_parts = [e for e in (berr, ferr) if e]
    bundle["board_err"] = "; ".join(board_parts)
    errs = [e for e in (qerr, bundle["board_err"]) if e]
    bundle["error"] = "; ".join(errs)

    _CACHE["key"] = key
    _CACHE["ts"] = now
    _CACHE["bundle"] = bundle
    return bundle


def clear_ma_zong_intraday_cache() -> None:
    _CACHE["key"] = ""
    _CACHE["ts"] = 0.0
    _CACHE["bundle"] = None
