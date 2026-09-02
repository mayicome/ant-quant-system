# -*- coding: utf-8 -*-
"""马总盘中逻辑：快速扫描满足条件候选（策略生成用）。

流水线：
  1) push2 按净流入降序早停 ≥ min_inflow_wan（性能预筛，软门槛仍在 select 复判）
  2) 硬门槛：当时涨幅（主板/创科北）
  3) 拉日线 + logic2 select() 复判五项软门槛；硬通过但未全满足的也输出
"""
from __future__ import annotations

import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

_LOGIC2_SELECT = None
_LOGIC2_NS: Dict[str, Any] = {}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_logic2_rule() -> Tuple[Any, Dict[str, Any]]:
    global _LOGIC2_SELECT, _LOGIC2_NS
    if callable(_LOGIC2_SELECT) and _LOGIC2_NS:
        return _LOGIC2_SELECT, _LOGIC2_NS
    src = _project_root() / "tools" / "_rule_src_ma_zong_logic2.py"
    ns: Dict[str, Any] = {}
    exec(compile(src.read_text(encoding="utf-8"), str(src), "exec"), ns, ns)
    fn = ns.get("select")
    if not callable(fn):
        raise RuntimeError("logic2 rule missing select()")
    _LOGIC2_SELECT = fn
    _LOGIC2_NS = ns
    return fn, ns


def _code6(v: Any) -> str:
    s = str(v or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def _is_growth_board(code: str) -> bool:
    c = _code6(code)
    return c.startswith(("300", "301", "688", "689", "8", "4", "920"))


def _pct_threshold(code: str, ns: Dict[str, Any]) -> float:
    main_lo = float(ns.get("MAIN_PCT_LO") or 6.0)
    growth_lo = float(ns.get("GROWTH_PCT_LO") or 10.0)
    return growth_lo if _is_growth_board(code) else main_lo


SOFT_COND_FIELDS = (
    ("条件_行业或概念排名达标", "板块排名"),
    ("条件_主力净流入>=3500万", "主力净流入"),
    ("条件_前10日无大涨", "前10日无大涨"),
    ("条件_价站上MA5且MA20", "价>MA5且MA20"),
    ("条件_当天未涨停过", "当天未涨停"),
)


def _board_thresholds(extra: Optional[Dict[str, Any]]) -> Tuple[int, int]:
    ind_n, con_n = 32, 8
    if not isinstance(extra, dict):
        return ind_n, con_n
    try:
        ind_n = int(extra.get("条件_行业前N") or extra.get("BOARD_TOP_N_INDUSTRY") or 32)
    except (TypeError, ValueError):
        pass
    try:
        con_n = int(extra.get("条件_概念前N") or extra.get("BOARD_TOP_N_CONCEPT") or 8)
    except (TypeError, ValueError):
        pass
    return ind_n, con_n


def _parse_hit_boards(extra: Optional[Dict[str, Any]]) -> List[Tuple[str, str, int]]:
    """解析 命中前8标签 → [(名称, 行业|概念, 名次)]。"""
    if not isinstance(extra, dict):
        return []
    hit = str(extra.get("命中前8标签") or "").strip()
    out: List[Tuple[str, str, int]] = []
    if hit:
        import re

        for m in re.finditer(r"([^;()]+)\((行业|概念)#(\d+)\)", hit):
            name = str(m.group(1) or "").strip()
            kind = str(m.group(2) or "").strip()
            try:
                rk = int(m.group(3))
            except (TypeError, ValueError):
                continue
            if name and kind and rk >= 1:
                out.append((name, kind, rk))
    if out:
        return out
    # 无命中串时退回所属最高行业/概念
    for kind, name_k, rk_k in (
        ("行业", "所属行业最高排名名称", "所属行业最高排名名次"),
        ("概念", "所属概念最高排名名称", "所属概念最高排名名次"),
    ):
        name = str(extra.get(name_k) or "").strip()
        rk = extra.get(rk_k)
        try:
            rki = int(rk)
        except (TypeError, ValueError):
            continue
        if name and rki >= 1:
            out.append((name, kind, rki))
    return out


def format_qualifying_board_line(extra: Optional[Dict[str, Any]]) -> str:
    """仅列出达到软门槛的板块（行业前N / 概念前N）及名次。"""
    if not isinstance(extra, dict):
        return ""
    ind_n, con_n = _board_thresholds(extra)
    parts: List[str] = []
    seen = set()
    for name, kind, rk in sorted(_parse_hit_boards(extra), key=lambda x: (x[1] != "行业", x[2], x[0])):
        ok = (kind == "行业" and rk <= ind_n) or (kind == "概念" and rk <= con_n)
        if not ok:
            continue
        key = (kind, name, rk)
        if key in seen:
            continue
        seen.add(key)
        parts.append("%s:%s#%d" % (kind, name, rk))
    return " | ".join(parts)


def format_board_rank_line(extra: Optional[Dict[str, Any]]) -> str:
    """所属排名最高的行业/概念板块及名次（不论是否过门槛）。"""
    if not isinstance(extra, dict):
        return ""
    parts: List[str] = []
    ind_name = str(extra.get("所属行业最高排名名称") or "").strip()
    ind_rk = extra.get("所属行业最高排名名次")
    if ind_name or ind_rk not in ("", None):
        rk_s = str(ind_rk) if ind_rk not in ("", None) else "?"
        parts.append("行业:%s#%s" % (ind_name or "?", rk_s))
    con_name = str(extra.get("所属概念最高排名名称") or "").strip()
    con_rk = extra.get("所属概念最高排名名次")
    if con_name or con_rk not in ("", None):
        rk_s = str(con_rk) if con_rk not in ("", None) else "?"
        parts.append("概念:%s#%s" % (con_name or "?", rk_s))
    hit = str(extra.get("命中前8标签") or "").strip()
    if hit and not parts:
        parts.append("命中:%s" % hit)
    return " | ".join(parts)


def summarize_qualifying_boards(rows: List[Dict[str, Any]]) -> List[Tuple[str, str, int, int]]:
    """硬门槛票中去重汇总满足门槛的板块。返回 [(kind, name, rank, stock_count)]。"""
    tally: Dict[Tuple[str, str, int], int] = {}
    for row in rows or []:
        extra = row.get("extra") if isinstance(row, dict) else None
        if not isinstance(extra, dict):
            extra = {}
        line = str((row or {}).get("qualifying_board_text") or "").strip()
        boards = _parse_hit_boards(extra)
        ind_n, con_n = _board_thresholds(extra)
        seen_row = set()
        for name, kind, rk in boards:
            ok = (kind == "行业" and rk <= ind_n) or (kind == "概念" and rk <= con_n)
            if not ok:
                continue
            key = (kind, name, rk)
            if key in seen_row:
                continue
            seen_row.add(key)
            tally[key] = tally.get(key, 0) + 1
        if not seen_row and line:
            # 兼容只带了文本的行
            pass
    out = [(k[0], k[1], k[2], n) for k, n in tally.items()]
    out.sort(key=lambda x: (0 if x[0] == "行业" else 1, x[2], -x[3], x[1]))
    return out


def format_soft_conditions(extra: Optional[Dict[str, Any]]) -> str:
    """五项软门槛是/否，便于日志对照。"""
    if not isinstance(extra, dict):
        return ""
    parts: List[str] = []
    for key, label in SOFT_COND_FIELDS:
        v = extra.get(key)
        mark = "是" if v else "否"
        parts.append("%s:%s" % (label, mark))
    return " | ".join(parts)


def _cheap_hard_pct_only(codes_quotes: Dict[str, Dict[str, Any]], ns: Dict[str, Any]) -> Tuple[List[str], int]:
    """硬门槛：仅当时涨幅（主板/创科北）。"""
    cheap: List[str] = []
    skip_pct = 0
    for c6, q in codes_quotes.items():
        pct = q.get("pct")
        thr = _pct_threshold(c6, ns)
        try:
            if pct is None or float(pct) + 1e-9 < float(thr):
                skip_pct += 1
                continue
        except (TypeError, ValueError):
            skip_pct += 1
            continue
        cheap.append(c6)
    return cheap, skip_pct


def _extract_ask_from_row(row: Any) -> float:
    if row is None:
        return 0.0
    try:
        av = row.get("askPrice") if hasattr(row, "get") else None
        if av is None and hasattr(row, "__getitem__"):
            av = row["askPrice"]
    except Exception:
        av = None
    if isinstance(av, (list, tuple)) and av:
        try:
            return float(av[0] or 0)
        except (TypeError, ValueError):
            return 0.0
    try:
        return float(av or 0)
    except (TypeError, ValueError):
        return 0.0


def resolve_ask1_price(
    code: str,
    *,
    prices: Optional[Dict[str, Dict[str, Any]]] = None,
    quote: Optional[Dict[str, Any]] = None,
    trade_date: Optional[date] = None,
) -> Tuple[float, str]:
    """最近 tick 卖一；缺 tick 时用 quote/现价兜底。返回 (ask1, source)。"""
    c6 = _code6(code)
    td = trade_date or date.today()
    prices = prices or {}

    p = prices.get(c6) or prices.get(code) or {}
    for key in ("ask1", "卖一价", "ask_price", "askPrice"):
        if key in p:
            try:
                v = float(p.get(key) or 0)
                if v > 0:
                    return v, "prices.%s" % key
            except (TypeError, ValueError):
                pass
    ap = p.get("askPrice")
    if isinstance(ap, (list, tuple)) and ap:
        try:
            v = float(ap[0] or 0)
            if v > 0:
                return v, "prices.askPrice[0]"
        except (TypeError, ValueError):
            pass

    try:
        from utils.tick_data_cache import read_tick_cache

        df = read_tick_cache(c6, td)
        if df is not None and len(df) > 0:
            ask = _extract_ask_from_row(df.iloc[-1])
            if ask > 0:
                return ask, "tick_cache"
    except Exception:
        pass

    try:
        import xtquant.xtdata as xtdata
        from utils.strategy_pool_watch import code_6_to_full

        full = code_6_to_full(c6)
        tmap = xtdata.get_full_tick([full])
        tk = tmap.get(full) if isinstance(tmap, dict) else None
        if isinstance(tk, dict):
            ap2 = tk.get("askPrice") or tk.get("ask_price")
            if isinstance(ap2, (list, tuple)) and ap2:
                v = float(ap2[0] or 0)
                if v > 0:
                    return v, "full_tick"
            for k in ("askPrice", "ask1"):
                try:
                    v = float(tk.get(k) or 0)
                    if v > 0:
                        return v, "full_tick.%s" % k
                except (TypeError, ValueError):
                    pass
    except Exception:
        pass

    q = quote or {}
    for k in ("price",):
        try:
            v = float(q.get(k) or 0)
            if v > 0:
                return v, "quote.%s" % k
        except (TypeError, ValueError):
            pass
    try:
        v = float(p.get("current") or p.get("最新价") or 0)
        if v > 0:
            return v, "prices.current"
    except (TypeError, ValueError):
        pass
    return 0.0, "none"


def ask1_plus_slippage(code: str, ask1: float) -> float:
    try:
        from core.utils.security_type import SecurityTypeUtil

        precision = int(SecurityTypeUtil.get_price_precision(_code6(code)))
        slippage = 0.001 if precision == 3 else 0.01
    except Exception:
        precision, slippage = 2, 0.01
    base = float(ask1)
    if base <= 0:
        return 0.0
    return round(base + slippage, precision)


def scan_logic2_meet_candidates(
    as_of: Optional[date] = None,
    *,
    force_live: Optional[bool] = None,
    min_inflow_wan: float = 3500.0,
    skip_em_hot: bool = True,
    progress=None,
) -> Dict[str, Any]:
    """扫描并返回 meet=True 的候选列表与统计。"""
    from utils.daily_cache_reader import load_daily_dataframe
    from utils.eastmoney_board_rank_ctx import _load_stock_info_tag_index
    from utils.ma_zong_intraday_ctx import clear_ma_zong_intraday_cache, load_ma_zong_intraday_bundle

    select_fn, ns = _load_logic2_rule()
    as_d = as_of or date.today()
    use_live = True if force_live is None else bool(force_live)
    if force_live is None:
        use_live = as_d == date.today()

    def _log(msg: str) -> None:
        if callable(progress):
            try:
                progress(str(msg))
            except Exception:
                pass
        print(msg)

    clear_ma_zong_intraday_cache()
    _log("[盘中扫描] 拉东财快照 inflow≥%.0f万 …" % float(min_inflow_wan))
    bundle = load_ma_zong_intraday_bundle(
        as_d,
        force_live=use_live,
        refresh=True,
        ttl_sec=1,
        min_inflow_wan=float(min_inflow_wan),
    )
    quotes = bundle.get("quotes") or {}
    if not quotes:
        err = str(bundle.get("error") or bundle.get("quote_err") or "无行情")
        return {
            "as_of": as_d.isoformat(),
            "mode": bundle.get("mode"),
            "fetched_at": bundle.get("fetched_at"),
            "error": err,
            "candidates": [],
            "stats": {},
        }

    _tag_to, code_tags = _load_stock_info_tag_index()
    em_hot = {}
    if not skip_em_hot:
        try:
            from utils.eastmoney_board_rank_ctx import load_em_board_hot_map

            em_hot = load_em_board_hot_map(
                as_d, top_n=50, rs_top_k=50, min_members=10, elig_bands=((1, 40),)
            )
        except Exception:
            em_hot = {}

    n_inflow = len(quotes)
    cheap, skip_pct = _cheap_hard_pct_only(quotes, ns)

    _log(
        "[盘中扫描] inflow池=%d → 硬涨幅通过=%d (skip涨幅=%d)"
        % (n_inflow, len(cheap), skip_pct)
    )

    meet_rows: List[Dict[str, Any]] = []
    near_miss: List[Dict[str, Any]] = []
    hard_pass_all: List[Dict[str, Any]] = []
    skip_daily = 0
    skip_hard = 0
    for i, c6 in enumerate(sorted(cheap), 1):
        if i == 1 or i % 20 == 0 or i == len(cheap):
            _log("[盘中扫描] 日线评估 %d/%d …" % (i, len(cheap)))
        q = quotes.get(c6) or {}
        name = str(q.get("name") or "")
        tags = list(code_tags.get(c6) or ())
        df = load_daily_dataframe(
            c6,
            through_date=as_d,
            allow_xtdata_fallback=False,
            allow_on_demand=False,
        )
        if df is None or getattr(df, "empty", True):
            skip_daily += 1
            import pandas as pd

            df = pd.DataFrame(columns=["date", "close"])
        ok, extra = select_fn(
            c6,
            name,
            tags,
            df,
            as_d,
            {"em_board_hot": em_hot, "inflow_rank": {}},
        )
        if not ok:
            skip_hard += 1
            continue
        extra = dict(extra) if isinstance(extra, dict) else {}
        row_base = {
            "code": c6,
            "name": name or str(extra.get("股票名称_行情") or ""),
            "quote": dict(q),
            "extra": extra,
            "conditions_text": format_soft_conditions(extra),
            "qualifying_board_text": format_qualifying_board_line(extra),
            "board_text": format_board_rank_line(extra),
            "reason": str(extra.get("不满足的原因") or "").strip(),
            "pct": q.get("pct"),
            "inflow_wan": q.get("inflow_wan"),
            "meet": bool(extra.get("满足条件")),
        }
        hard_pass_all.append(row_base)
        if row_base["meet"]:
            meet_rows.append(row_base)
        else:
            near_miss.append(row_base)

    _log(
        "[盘中扫描] 硬门槛通过=%d 满足条件=%d 软门槛未全满足=%d"
        % (len(hard_pass_all), len(meet_rows), len(near_miss))
    )
    return {
        "as_of": as_d.isoformat(),
        "mode": bundle.get("mode"),
        "fetched_at": bundle.get("fetched_at"),
        "error": str(bundle.get("error") or ""),
        "candidates": meet_rows,
        "near_miss": near_miss,
        "hard_pass_all": hard_pass_all,
        "stats": {
            "quotes_inflow_pool": n_inflow,
            "cheap_pass": len(cheap),
            "skip_pct": skip_pct,
            "skip_no_daily": skip_daily,
            "skip_hard_in_select": skip_hard,
            "hard_pass": len(hard_pass_all),
            "near_miss": len(near_miss),
            "meet": len(meet_rows),
        },
    }
