# -*- coding: utf-8 -*-
"""马总选股逻辑2：盘中涨幅 / 主力净流入 / 板块涨幅榜快照。

数据源：东财 push2（与 utils.eastmoney_fund_flow、tools.snapshot_eastmoney_board_rank 同源）。
盘中（选股日=今天）拉实时；历史日回退本地 CSV。
同一进程内按 TTL 缓存，避免选股引擎对每只股票重复拉全市场。
"""
from __future__ import annotations

import os
import time
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any, Dict, List, Optional, Tuple, Union

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


def _quote_from_push2_row(r: dict) -> Tuple[str, Dict[str, Any]]:
    """解析东财 push2 diff 单行 → (code6, quote_dict)。"""
    from utils.main_force_inflow_rank import yuan_to_display

    c6 = _code6(r.get("f12"))
    if not c6:
        return "", {}
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
    return c6, {
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


def _fetch_quotes_live_min_inflow(min_inflow_wan: float) -> Tuple[List[dict], dict]:
    """按主力净流入降序翻页，整页均低于 min_inflow_wan 时早停。"""
    import time as _time

    from utils.eastmoney_fund_flow import _get_json, _session, _UT, _FS, _FIELDS

    session = _session()
    pz = 100
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
    rows: List[dict] = []
    pn = 0
    stopped_early = False
    max_pages = 200  # 安全上限，正常在 inflow 跌破阈值时早停

    while pn < max_pages:
        pn += 1
        params = dict(params)
        params["pn"] = str(pn)
        payload = _get_json(session, params)
        data = payload.get("data") or {}
        diff = data.get("diff")
        page_rows: List[dict] = []
        if diff:
            if isinstance(diff, dict):
                page_rows = list(diff.values())
            else:
                page_rows = list(diff)
        if not page_rows:
            break
        page_kept = 0
        for r in page_rows:
            try:
                main_yuan = float(r.get("f62") or 0.0)
            except (TypeError, ValueError):
                main_yuan = 0.0
            inflow_wan = main_yuan / 1e4
            if inflow_wan + 1e-9 >= float(min_inflow_wan):
                rows.append(r)
                page_kept += 1
        if page_kept == 0:
            stopped_early = True
            break
        if pn < max_pages:
            _time.sleep(0.08)

    meta = {
        "total": len(rows),
        "fetched": len(rows),
        "pages": pn,
        "page_size": pz,
        "stopped_early": stopped_early,
        "min_inflow_wan": float(min_inflow_wan),
    }
    return rows, meta


def _load_quotes_live(min_inflow_wan: Optional[float] = None) -> Tuple[Dict[str, Dict[str, Any]], str]:
    """拉全市场 push2 个股快照；min_inflow_wan 设则按 f62 降序早停（低于阈值后不再翻页）。"""
    from utils.eastmoney_fund_flow import fetch_individual_fund_flow_rows

    min_wan = None
    if min_inflow_wan is not None:
        try:
            min_wan = float(min_inflow_wan)
        except (TypeError, ValueError):
            min_wan = None

    try:
        if min_wan is not None and min_wan > 0:
            rows, meta = _fetch_quotes_live_min_inflow(min_wan)
        else:
            rows, meta = fetch_individual_fund_flow_rows()
    except Exception as e:
        return {}, "东财资金流拉取失败:%s" % e

    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        c6, qd = _quote_from_push2_row(r)
        if c6 and qd:
            out[c6] = qd
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



def is_a_share_intraday_session(now: Optional[datetime] = None) -> bool:
    """A-share intraday session: trading day and 09:30-15:00 Beijing (incl. lunch).

    Live eastmoney data is meaningful only in this window; lunch still counts
    as in-session so we do NOT fall back to stale CSV.
    """
    now = now or datetime.now()
    try:
        from utils.trading_day import is_tradeday
    except Exception:
        if now.weekday() >= 5:
            return False
        t = now.time()
        return dt_time(9, 30) <= t <= dt_time(15, 0)
    if not is_tradeday(now.date()):
        return False
    t = now.time()
    return dt_time(9, 30) <= t <= dt_time(15, 0)


def resolve_ma_zong_data_mode(
    as_of: DateLike = None,
    *,
    force_live: Optional[bool] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Decide as_of / whether live / reason.

    Rules:
      - live only when as_of==today AND currently in intraday session
      - otherwise use previous trading day's local files
      - force_live=True outside session is demoted (avoid hang)
    """
    now = now or datetime.now()
    today = now.date()
    as_d = _as_date(as_of) or today
    in_session = is_a_share_intraday_session(now)

    requested = force_live
    if force_live is None:
        want_live = (as_d == today) and in_session
    else:
        want_live = bool(force_live)

    use_live = bool(want_live) and (as_d == today) and in_session
    reason = "live_session" if use_live else "file"
    effective_asof = as_d

    if use_live:
        return {
            "as_of": effective_asof,
            "use_live": True,
            "in_session": in_session,
            "reason": reason,
            "requested_force_live": requested,
        }

    if as_d == today or (not use_live and as_d >= today):
        try:
            from utils.trading_day import previous_tradeday, last_tradeday_on_or_before

            if as_d == today:
                effective_asof = previous_tradeday(today)
            else:
                effective_asof = last_tradeday_on_or_before(as_d) or as_d
        except Exception:
            d = as_d - timedelta(days=1) if as_d == today else as_d
            for _ in range(10):
                if d.weekday() < 5:
                    effective_asof = d
                    break
                d -= timedelta(days=1)

    if requested is True and not use_live:
        reason = "demote_force_live_outside_session"
    elif as_d == today and not in_session:
        reason = "outside_session_use_prev_tradeday"
    elif as_d != today:
        reason = "historical_file"
    else:
        reason = "file"

    return {
        "as_of": effective_asof,
        "use_live": False,
        "in_session": in_session,
        "reason": reason,
        "requested_force_live": requested,
    }


def load_ma_zong_intraday_bundle(
    as_of: DateLike = None,
    *,
    force_live: Optional[bool] = None,
    ttl_sec: float = _DEFAULT_TTL_SEC,
    refresh: bool = False,
    min_inflow_wan: Optional[float] = None,
) -> Dict[str, Any]:
    """Return intraday/file snapshot bundle.

    min_inflow_wan: live mode early-stop by inflow (default None=full).
    force_live:
      None  -> live only if today AND in session; else prev trading day file
      True  -> real live only in session; outside session demote to file
      False -> force file

    When live fetch fails:
      - in session: do NOT fall back to CSV; keep error
      - outside session: fall back to prev trading day CSV, mode=file_fallback
    """
    now = datetime.now()
    resolved = resolve_ma_zong_data_mode(as_of, force_live=force_live, now=now)
    as_d = resolved["as_of"]
    use_live = bool(resolved["use_live"])
    in_session = bool(resolved["in_session"])
    reason = str(resolved.get("reason") or "")

    key = "%s:%s" % (as_d.isoformat(), "live" if use_live else "file")
    now_ts = time.time()
    if (
        not refresh
        and _CACHE.get("key") == key
        and isinstance(_CACHE.get("bundle"), dict)
        and (now_ts - float(_CACHE.get("ts") or 0.0)) < float(ttl_sec)
    ):
        return _CACHE["bundle"]

    mode = "live" if use_live else "file"
    bundle = _empty_bundle(as_d, mode=mode)
    bundle["fetched_at"] = now.strftime("%Y-%m-%d %H:%M:%S")
    bundle["mode_reason"] = reason
    bundle["in_session"] = in_session
    if reason and reason != "live_session":
        print(
            "[马总盘中] data_mode=%s as_of=%s reason=%s in_session=%s"
            % (mode, as_d.isoformat(), reason, in_session)
        )

    if use_live:
        quotes, qerr = _load_quotes_live(min_inflow_wan=min_inflow_wan)
        ind, con, ind_chg, con_chg, berr = _load_board_ranks_live()
        ind_fr, con_fr, ferr = _load_board_flow_ratio_live()
        # In session: never fall back to CSV (including board fund-flow).
        live_failed = (not quotes) or bool(qerr)
        boards_empty = (not ind and not con) and (not ind_fr and not con_fr)
        if live_failed or (boards_empty and (berr or ferr)):
            if in_session:
                pass
            else:
                try:
                    from utils.trading_day import previous_tradeday

                    fb_d = previous_tradeday(now.date())
                except Exception:
                    fb_d = as_d
                print(
                    "[马总盘中] live failed; off-session CSV fallback mode=file_fallback as_of=%s; live_err=%s"
                    % (
                        fb_d.isoformat(),
                        "; ".join(e for e in (qerr, berr, ferr) if e),
                    )
                )
                quotes2, qerr2 = _load_quotes_file(fb_d)
                ind2, con2, ind_chg2, con_chg2, berr2 = _load_board_ranks_file(fb_d)
                ind_fr2, con_fr2, ferr2 = _load_board_flow_ratio_file(fb_d)
                if quotes2:
                    quotes, qerr = quotes2, ""
                    as_d = fb_d
                    bundle["as_of"] = as_d
                    bundle["mode"] = "file_fallback"
                    mode = "file_fallback"
                if ind2 or con2:
                    ind, con, ind_chg, con_chg = ind2, con2, ind_chg2, con_chg2
                    berr = "" if (ind2 or con2) else (berr2 or berr)
                if ind_fr2 or con_fr2:
                    ind_fr, con_fr = ind_fr2, con_fr2
                    ferr = "" if (ind_fr2 or con_fr2) else (ferr2 or ferr)
                if not quotes2:
                    qerr = qerr or qerr2
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
    bundle["mode"] = mode

    _CACHE["key"] = "%s:%s" % (as_d.isoformat(), "file" if mode == "file_fallback" else mode)
    _CACHE["ts"] = now_ts
    _CACHE["bundle"] = bundle
    return bundle


def clear_ma_zong_intraday_cache() -> None:
    _CACHE["key"] = ""
    _CACHE["ts"] = 0.0
    _CACHE["bundle"] = None
