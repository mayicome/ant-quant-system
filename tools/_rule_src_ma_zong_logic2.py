# 马总选股逻辑2（盘中，建议 14:40 运行）
# 硬入选（不进结果表则跳过）：
#   - 当时涨幅 主板>=7%、创业/科创/北交所>=13%
#   - 近 EX_DIV_LOOKBACK 交易日无疑似除权开盘缺口（整表回测/因子筛选不应带上）
# 满足条件（全部为真才 True；前10日窗口不含当日）：
#   1) 所属行业东财涨幅原始排名前 BOARD_TOP_N_INDUSTRY，或概念前 BOARD_TOP_N_CONCEPT（任一）
#   2) 当时主力净流入 >= 5000万
#   3) 前10个交易日：主板无涨幅>=5%；创业/科创/北交所无涨幅>=10%
#   4) 当时价 > MA5 且 > MA20（MA 用日线，今日收盘用当时价替换）
#   5) 当天未涨停过（当时涨停，或今日最高价曾触及涨停价 → 满足条件=False；仍可因硬门槛入选）
# 盘中数据：utils.ma_zong_intraday_ctx（东财 push2 实时涨幅+主力净流入+板块榜）
# 历史选股日：回退本地 CSV；涨幅缺省时用日线收盘算
# 另保留 besttest 热门诊断字段（ctx em_board_hot，非入选门槛）
# 引擎：关闭热门池收窄，否则缺今日行业榜时候选变 0
USE_EM_CANDIDATE_POOL = False
TOP_N = 50
RS_TOP_K = 50
RS_LO = 1
RS_HI = 50
RS_TOP_FRAC_NUM = 1
RS_TOP_FRAC_DEN = 2
RS_LOOKBACK = 10
MIN_MEMBERS = 10
MA_GAP_LO = 0.005
MA_GAP_HI = 0.02
ELIG_LO = 1
ELIG_HI_SECTOR = 30
ELIG_HI_CONCEPT = 30
ELIG_HI = 40
MIN_FLOAT_MV_YI = 120.0
ANY_TAG = False
REQUIRE_MIN_FLOAT_MV = False
REQUIRE_MA_GAP = False
REQUIRE_MA_LT_ALIGN = False
REQUIRE_NO_RECENT_LU = False
APPLY_STOCK_FILTERS = False
HOT_MODE = "ma_zong_logic2_intraday"
N = 20
BOARD_TOP_N_INDUSTRY = 32  # 满足条件：所属行业东财涨幅排名门槛
BOARD_TOP_N_CONCEPT = 8  # 满足条件：所属概念东财涨幅排名门槛
BOARD_TOP_N = BOARD_TOP_N_INDUSTRY  # 兼容旧字段（取行业门槛）
BOARD_SHOW_TOP_N = 30  # 展示用名次上限（不改进入选门槛）
MIN_INFLOW_WAN = 5000.0
PRIOR_LOOKBACK = 10
EX_DIV_LOOKBACK = 20
EX_DIV_EPS = 0.005
MAIN_PCT_LO = 7.0
GROWTH_PCT_LO = 13.0

_STOCK_TAG_CACHE = {"loaded": False, "code_to_tags": {}}


def _code6(stock_code):
    s = str(stock_code or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def _as_date(d):
    """规范化选股日；None 视为今天（引擎「今天」模式会传 None）。"""
    from datetime import date as _date, datetime as _dt
    if d is None:
        return _date.today()
    if isinstance(d, _dt):
        return d.date()
    if isinstance(d, _date):
        return d
    try:
        if hasattr(d, "to_pydatetime"):
            return d.to_pydatetime().date()
    except Exception:
        pass
    try:
        if hasattr(d, "date") and callable(d.date):
            return d.date()
    except Exception:
        pass
    try:
        import pandas as _pd
        ts = _pd.Timestamp(d)
        if _pd.isna(ts):
            return None
        return ts.date()
    except Exception:
        return None

def _is_growth_board(stock_code):
    code = str(stock_code or "").strip()
    if "." in code:
        code = code.split(".", 1)[0]
    return code.startswith(("300", "301", "688", "689", "8", "4", "920"))


def _pct_threshold(stock_code):
    return float(GROWTH_PCT_LO) if _is_growth_board(stock_code) else float(MAIN_PCT_LO)


def _limit_up_pct(stock_code, stock_name, as_of_date=None):
    """当日涨停幅度阈值（百分比，如主板 10.0）。"""
    from datetime import date as _date
    name = str(stock_name or "").upper()
    code = str(stock_code or "").strip()
    if "." in code:
        code = code.split(".", 1)[0]
    if code.startswith(("300", "301", "688", "689")):
        return 20.0
    if code.startswith(("8", "4", "920")):
        return 30.0
    if "ST" in name:
        ref = _as_date(as_of_date) if as_of_date is not None else _date.today()
        if ref is None:
            ref = _date.today()
        if ref >= _date(2026, 7, 6):
            return 10.0
        return 5.0
    return 10.0


def _is_limit_up_now(pct, stock_code, stock_name, as_of_date=None):
    if pct is None:
        return False
    try:
        p = float(pct)
    except (TypeError, ValueError):
        return False
    lim = _limit_up_pct(stock_code, stock_name, as_of_date)
    # 与日线涨停判定类似：达到涨停幅度的 99% 即视作涨停
    return p >= float(lim) * 0.99


def _limit_up_price(pre_close, stock_code, stock_name, as_of_date=None):
    try:
        pc = float(pre_close)
    except (TypeError, ValueError):
        return None
    if pc <= 0:
        return None
    lim = float(_limit_up_pct(stock_code, stock_name, as_of_date)) / 100.0
    return round(pc * (1.0 + lim), 2)


def _day_high_and_pre_close(quote, daily_data, as_of_date):
    """优先行情 high/pre_close，否则用日线当日最高与昨收。"""
    high = None
    pre_close = None
    if isinstance(quote, dict):
        try:
            if quote.get("high") not in (None, ""):
                high = float(quote.get("high"))
        except (TypeError, ValueError):
            high = None
        try:
            if quote.get("pre_close") not in (None, ""):
                pre_close = float(quote.get("pre_close"))
        except (TypeError, ValueError):
            pre_close = None
    as_d = _as_date(as_of_date)
    dd = _sorted_daily(daily_data, as_of_date)
    if dd is None or as_d is None:
        return high, pre_close
    dcol = "_d" if "_d" in dd.columns else "date"
    if dcol == "_d":
        today_rows = dd[dd["_d"] == as_d]
        prev_rows = dd[dd["_d"] < as_d]
    else:
        today_rows = dd[dd["date"].map(_as_date) == as_d]
        prev_rows = dd[dd["date"].map(_as_date) < as_d]
    if high is None and today_rows is not None and not today_rows.empty:
        try:
            high = float(today_rows.iloc[-1].get("high"))
        except (TypeError, ValueError, KeyError):
            high = None
    if pre_close is None and prev_rows is not None and not prev_rows.empty:
        try:
            pre_close = float(prev_rows.iloc[-1].get("close"))
        except (TypeError, ValueError, KeyError):
            pre_close = None
    return high, pre_close


def _touched_limit_up_today(pct, quote, daily_data, stock_code, stock_name, as_of_date):
    """当天是否涨停过：当时已涨停，或今日最高价曾触及涨停价。"""
    if _is_limit_up_now(pct, stock_code, stock_name, as_of_date):
        return True
    high, pre_close = _day_high_and_pre_close(quote, daily_data, as_of_date)
    lu = _limit_up_price(pre_close, stock_code, stock_name, as_of_date)
    if high is None or lu is None:
        return False
    # 最高价达到涨停价（允许 1 分钱误差）
    return float(high) + 1e-6 >= float(lu) - 0.01


def _big_move_threshold(stock_code):
    return 0.10 if _is_growth_board(stock_code) else 0.05


def _ma(closes, n):
    if closes is None or len(closes) < n:
        return None
    return float(sum(closes[-n:])) / float(n)


def _sorted_daily(daily_data, as_of_date):
    if daily_data is None or getattr(daily_data, "empty", True):
        return None
    if "date" not in daily_data.columns or "close" not in daily_data.columns:
        return None
    dd = daily_data.copy()
    dd = dd.assign(_d=dd["date"].map(_as_date))
    dd = dd.dropna(subset=["_d"])
    if dd.empty:
        return None
    as_d = _as_date(as_of_date)
    if as_d is not None:
        dd = dd[dd["_d"] <= as_d]
    if dd is None or getattr(dd, "empty", True):
        return None
    return dd.sort_values("_d")

def _closes_list(daily_data, as_of_date, live_price=None):
    dd = _sorted_daily(daily_data, as_of_date)
    if dd is None:
        return None
    as_d = _as_date(as_of_date)
    closes = []
    for _, r in dd.iterrows():
        try:
            c = float(r.get("close"))
        except (TypeError, ValueError):
            continue
        if c == c and c > 0:
            closes.append((_as_date(r.get("date")), c))
    if not closes:
        return None
    vals = [c for _, c in closes]
    if live_price is not None and as_d is not None:
        try:
            lp = float(live_price)
        except (TypeError, ValueError):
            lp = None
        if lp is not None and lp > 0:
            if closes[-1][0] == as_d:
                vals[-1] = lp
            else:
                vals.append(lp)
    return vals


def _daily_pct_today(daily_data, as_of_date):
    dd = _sorted_daily(daily_data, as_of_date)
    if dd is None:
        return None, None
    as_d = _as_date(as_of_date)
    row = None
    if as_d is not None:
        for _, r in dd.iloc[::-1].iterrows():
            if _as_date(r.get("date")) == as_d:
                row = r
                break
    if row is None:
        return None, None
    prev = dd[(dd["_d"] if "_d" in dd.columns else dd["date"].map(_as_date)) < _as_date(row.get("_d") or row.get("date"))]
    if prev.empty:
        return None, None
    try:
        pc = float(prev.iloc[-1]["close"])
        cl = float(row["close"])
    except (TypeError, ValueError):
        return None, None
    if pc <= 0:
        return None, None
    return (cl / pc - 1.0) * 100.0, cl


def _prior_session_rets(daily_data, as_of_date, lookback):
    """不含当日的前 lookback 个交易日涨幅列表（小数）。

    只取 as_of 之前最后 lookback+1 根K线再算涨幅，避免把当日涨停算进窗口。
    """
    as_d = _as_date(as_of_date)
    if as_d is None:
        return []
    dd = _sorted_daily(daily_data, as_of_date)
    if dd is None:
        return []
    # 优先用规范化列 _d；兼容旧数据仅有 date
    dcol = "_d" if "_d" in dd.columns else "date"
    prev = dd[dd[dcol].map(_as_date) < as_d] if dcol == "date" else dd[dd[dcol] < as_d]
    if prev is None or getattr(prev, "empty", True) or len(prev) < 2:
        return []
    lb = max(1, int(lookback))
    window = prev.tail(lb + 1)
    closes = []
    for _, r in window.iterrows():
        try:
            c = float(r.get("close"))
        except (TypeError, ValueError):
            continue
        if c == c and c > 0:
            closes.append(c)
    if len(closes) < 2:
        return []
    rets = []
    for i in range(1, len(closes)):
        pc = closes[i - 1]
        if pc <= 0:
            continue
        rets.append((closes[i] - pc) / pc)
    return rets


def _prior_max_ret_with_date(daily_data, as_of_date, lookback):
    """(最大涨幅小数, 日期)；窗口不含当日。"""
    as_d = _as_date(as_of_date)
    if as_d is None:
        return None, None
    dd = _sorted_daily(daily_data, as_of_date)
    if dd is None:
        return None, None
    dcol = "_d" if "_d" in dd.columns else "date"
    prev = dd[dd[dcol].map(_as_date) < as_d] if dcol == "date" else dd[dd[dcol] < as_d]
    if prev is None or getattr(prev, "empty", True) or len(prev) < 2:
        return None, None
    lb = max(1, int(lookback))
    window = prev.tail(lb + 1)
    best = None
    best_d = None
    closes = []
    dates = []
    for _, r in window.iterrows():
        try:
            c = float(r.get("close"))
        except (TypeError, ValueError):
            continue
        d = _as_date(r.get("_d") if "_d" in window.columns else r.get("date"))
        if c == c and c > 0 and d is not None:
            closes.append(c)
            dates.append(d)
    for i in range(1, len(closes)):
        pc = closes[i - 1]
        if pc <= 0:
            continue
        ret = (closes[i] - pc) / pc
        if best is None or ret > best:
            best = ret
            best_d = dates[i]
    return best, best_d


def _is_big_move_ret(ret, thr):
    """是否达到 X%及以上（万分位四舍五入，避免浮点边界误判）。"""
    try:
        return round(float(ret) * 10000.0) >= round(float(thr) * 10000.0)
    except (TypeError, ValueError):
        return False


def _prior_ex_div_gap(stock_code, stock_name, daily_data, as_of_date, lookback):
    """近 lookback 个交易日（不含当日）是否出现疑似除权开盘缺口。

    判定：开盘/昨收 - 1 < -(涨跌停幅度 + EX_DIV_EPS)。
    返回 (无缺口合格?, 最深缺口小数或None, 缺口日或None)。
    """
    as_d = _as_date(as_of_date)
    if as_d is None:
        return True, None, None
    dd = _sorted_daily(daily_data, as_of_date)
    if dd is None or "open" not in getattr(dd, "columns", []):
        return True, None, None
    dcol = "_d" if "_d" in dd.columns else "date"
    if dcol == "date":
        prev = dd[dd[dcol].map(_as_date) < as_d]
    else:
        prev = dd[dd[dcol] < as_d]
    if prev is None or getattr(prev, "empty", True) or len(prev) < 2:
        return True, None, None
    lb = max(1, int(lookback))
    pre = prev.tail(lb + 1)
    closes_by_d = {}
    opens_by_d = {}
    dates = []
    for _, r in pre.iterrows():
        try:
            d = _as_date(r.get("_d") if "_d" in pre.columns else r.get("date"))
            o = float(r.get("open"))
            c = float(r.get("close"))
        except (TypeError, ValueError):
            continue
        if d is None or not (o == o and o > 0 and c == c and c > 0):
            continue
        closes_by_d[d] = c
        opens_by_d[d] = o
        dates.append(d)
    dates = sorted(set(dates))
    check_dates = [d for d in dates if d < as_d][-lb:]
    worst = None
    worst_d = None
    for d in check_dates:
        prev_closes = [closes_by_d[x] for x in dates if x < d]
        if not prev_closes:
            continue
        pc = float(prev_closes[-1])
        o = float(opens_by_d.get(d) or 0)
        if pc <= 0 or o <= 0:
            continue
        gap = (o / pc) - 1.0
        lr = float(_limit_up_pct(stock_code, stock_name, d)) / 100.0
        thr = -(lr + float(EX_DIV_EPS))
        if gap < thr:
            if worst is None or gap < worst:
                worst = gap
                worst_d = d
    return worst is None, worst, worst_d


def _load_stock_tags():
    if _STOCK_TAG_CACHE["loaded"]:
        return _STOCK_TAG_CACHE["code_to_tags"]
    out = {}
    try:
        from utils.eastmoney_board_rank_ctx import _load_stock_info_tag_index
        _tag_to_codes, code_to_tags = _load_stock_info_tag_index()
        if isinstance(code_to_tags, dict):
            out = code_to_tags
    except Exception:
        out = {}
    _STOCK_TAG_CACHE["code_to_tags"] = out
    _STOCK_TAG_CACHE["loaded"] = True
    return out


def _bundle(as_of_date):
    from utils.ma_zong_intraday_ctx import load_ma_zong_intraday_bundle
    return load_ma_zong_intraday_bundle(as_of_date)


def _stock_tags(stock_code, sectors):
    tags = set(_load_stock_tags().get(_code6(stock_code)) or set())
    if sectors:
        for s in sectors:
            t = str(s or "").strip()
            if t:
                tags.add(t)
    return tags


def _best_owned_board(tags, rank_map, chg_map):
    """所属标签里东财原始名次最好的一项；跳过 em_board_exclude 非产业板。

    返回 (排名, 名称, 涨跌幅%)；无命中则 (None, "", None)。
    """
    try:
        from utils.em_board_exclude import is_excluded_em_board
    except Exception:
        def is_excluded_em_board(_n):
            return False

    best_rk = None
    best_name = ""
    best_chg = None
    for t in tags or ():
        name = str(t or "").strip()
        if not name or name not in (rank_map or {}):
            continue
        if is_excluded_em_board(name):
            continue
        try:
            rk = int(rank_map[name])
        except (TypeError, ValueError):
            continue
        if rk < 1:
            continue
        if best_rk is None or rk < best_rk:
            best_rk = rk
            best_name = name
            chg = (chg_map or {}).get(name)
            try:
                best_chg = None if chg is None else float(chg)
            except (TypeError, ValueError):
                best_chg = None
    return best_rk, best_name, best_chg


def _owned_board_extra_fields(stock_code, sectors, bundle):
    tags = _stock_tags(stock_code, sectors)
    ind_rk, ind_name, ind_chg = _best_owned_board(
        tags, (bundle or {}).get("ind_rank") or {}, (bundle or {}).get("ind_chg") or {}
    )
    con_rk, con_name, con_chg = _best_owned_board(
        tags, (bundle or {}).get("con_rank") or {}, (bundle or {}).get("con_chg") or {}
    )
    ind_fr = (bundle or {}).get("ind_flow_ratio") or {}
    con_fr = (bundle or {}).get("con_flow_ratio") or {}
    ind_ratio = ind_fr.get(ind_name) if ind_name else None
    con_ratio = con_fr.get(con_name) if con_name else None
    try:
        ind_ratio = None if ind_ratio is None else float(ind_ratio)
    except (TypeError, ValueError):
        ind_ratio = None
    try:
        con_ratio = None if con_ratio is None else float(con_ratio)
    except (TypeError, ValueError):
        con_ratio = None
    return {
        "所属行业最高排名名次": "" if ind_rk is None else int(ind_rk),
        "所属行业最高排名名称": ind_name or "",
        "所属行业最高排名涨幅": "" if ind_chg is None else round(float(ind_chg), 4),
        "所属行业最高排名净占比": "" if ind_ratio is None else round(float(ind_ratio), 4),
        "所属概念最高排名名次": "" if con_rk is None else int(con_rk),
        "所属概念最高排名名称": con_name or "",
        "所属概念最高排名涨幅": "" if con_chg is None else round(float(con_chg), 4),
        "所属概念最高排名净占比": "" if con_ratio is None else round(float(con_ratio), 4),
    }


def _board_top_hit(stock_code, sectors, bundle):
    ind_rank = (bundle or {}).get("ind_rank") or {}
    con_rank = (bundle or {}).get("con_rank") or {}
    tags = _stock_tags(stock_code, sectors)
    best_rank = None
    best_name = ""
    best_kind = ""
    show_hits = []
    all_hits = []
    cond_ok = False
    show_n = int(BOARD_SHOW_TOP_N)
    cond_n_ind = int(BOARD_TOP_N_INDUSTRY)
    cond_n_con = int(BOARD_TOP_N_CONCEPT)
    for t in tags:
        for rk, kind in ((ind_rank.get(t), "行业"), (con_rank.get(t), "概念")):
            if rk is None:
                continue
            try:
                rki = int(rk)
            except Exception:
                continue
            if rki < 1:
                continue
            all_hits.append((t, rki, kind))
            if best_rank is None or rki < best_rank:
                best_rank, best_name, best_kind = rki, t, kind
            if rki <= show_n:
                show_hits.append((t, rki, kind))
            if kind == "行业" and rki <= cond_n_ind:
                cond_ok = True
            elif kind == "概念" and rki <= cond_n_con:
                cond_ok = True
    src = show_hits if show_hits else all_hits
    hit_str = ";".join(
        "%s(%s#%d)" % (n, k, r) for n, r, k in sorted(src, key=lambda x: x[1])[:12]
    )
    return cond_ok, best_rank, best_name, best_kind, hit_str


def _empty_hot_fields():
    return {
        "合格榜内序位": "",
        "合格榜对应标签": "",
        "合格榜标签类型": "",
        "合格榜标签东财排名": "",
        "合格榜标签内RS排名": "",
        "合格榜标签RS样本数": "",
        "合格榜标签RS截断": "",
        "选出标签": "",
        "选出标签类型": "",
        "选出标签合格榜内序位": "",
        "选出标签东财排名": "",
        "选出标签内RS排名": "",
        "选出标签RS样本数": "",
        "选出标签RS截断": "",
        "今日热门板块最高排名A": "",
        "今日热门概念最高排名B": "",
        "在A中的RS排名": "",
        "在B中的RS排名": "",
        "A对应板块": "",
        "B对应概念": "",
        "RS最好的热门板块或概念": "",
        "在其中的RS排名": "",
        "近10日RS": "",
        "命中今日热门标签数": "",
        "流通市值_亿": "",
        "东财榜日期D": "",
        "东财榜日期D-1": "",
    }


def _hot_fields_from_ctx(stock_code, ctx):
    out = _empty_hot_fields()
    em = (ctx or {}).get("em_board_hot") or {}
    if not isinstance(em, dict) or not em:
        return out
    out["东财榜日期D"] = str(em.get("as_of") or "")
    out["东财榜日期D-1"] = str(em.get("prev_date") or "")
    c6 = _code6(stock_code)
    hits = em.get("today_code_hits") or {}
    hit = hits.get(c6) if isinstance(hits, dict) else None
    mv_map = em.get("float_mv_yi") or {}
    if isinstance(mv_map, dict):
        try:
            raw = mv_map.get(c6)
            if raw is not None and raw != "":
                out["流通市值_亿"] = round(float(raw), 2)
        except (TypeError, ValueError):
            pass
    if not isinstance(hit, dict):
        return out
    tag = hit.get("合格榜对应标签", "")
    kind = hit.get("合格榜标签类型", "")
    em_rank = hit.get("合格榜标签东财排名", "")
    elig = hit.get("合格榜内序位", "")
    rs_in_tag = hit.get("合格榜标签内RS排名", "")
    tag_rs_n = hit.get("合格榜标签RS样本数", "")
    out.update({
        "合格榜内序位": elig,
        "合格榜对应标签": tag,
        "合格榜标签类型": kind,
        "合格榜标签东财排名": em_rank,
        "合格榜标签内RS排名": rs_in_tag,
        "合格榜标签RS样本数": tag_rs_n,
        "选出标签": tag,
        "选出标签类型": kind,
        "选出标签合格榜内序位": elig,
        "选出标签东财排名": em_rank,
        "选出标签内RS排名": rs_in_tag,
        "选出标签RS样本数": tag_rs_n,
        "今日热门板块最高排名A": hit.get("今日热门板块最高排名A", ""),
        "今日热门概念最高排名B": hit.get("今日热门概念最高排名B", ""),
        "在A中的RS排名": hit.get("在A中的RS排名", ""),
        "在B中的RS排名": hit.get("在B中的RS排名", ""),
        "A对应板块": hit.get("A对应板块", ""),
        "B对应概念": hit.get("B对应概念", ""),
        "RS最好的热门板块或概念": hit.get("RS最好的热门板块或概念", ""),
        "在其中的RS排名": hit.get("在其中的RS排名", ""),
        "近10日RS": hit.get("近10日RS", ""),
        "命中今日热门标签数": hit.get("命中今日热门标签数", ""),
    })
    return out


def _fmt_num(v, nd=2):
    if v is None or v == "":
        return "无"
    try:
        x = float(v)
        if nd <= 0:
            return str(int(round(x)))
        return ("%." + str(int(nd)) + "f") % x
    except (TypeError, ValueError):
        return str(v)


def _board_fail_actual_detail(owned, board_err=""):
    """入选失败时展示实际行业/概念最高名次（全榜，不限前N）。"""
    parts = []
    ir = (owned or {}).get("所属行业最高排名名次")
    iname = (owned or {}).get("所属行业最高排名名称") or ""
    if ir not in ("", None):
        parts.append("行业%s#%s" % (iname, ir))
    cr = (owned or {}).get("所属概念最高排名名次")
    cname = (owned or {}).get("所属概念最高排名名称") or ""
    if cr not in ("", None):
        parts.append("概念%s#%s" % (cname, cr))
    if parts:
        return "、".join(parts)
    detail = "无所属行业/概念可匹配东财榜"
    if board_err:
        detail += "(%s)" % board_err
    return detail


def _fail_reasons_logic2(
    *,
    cond_board,
    cond_inflow,
    cond_prior,
    cond_ma,
    cond_not_lu,
    owned_board,
    inflow_wan,
    max_prior,
    bthr,
    prior_rets,
    price,
    ma5,
    ma20,
    pct,
    high_px,
    lu_px,
    board_err,
    quote_err,
):
    reasons = []
    if not cond_board:
        reasons.append(
            "行业或概念排名不满足，要求行业前%d或概念前%d，实际%s"
            % (
                int(BOARD_TOP_N_INDUSTRY),
                int(BOARD_TOP_N_CONCEPT),
                _board_fail_actual_detail(owned_board, board_err),
            )
        )
    if not cond_inflow:
        if inflow_wan is None:
            detail = "无数据"
            if quote_err:
                detail += "(%s)" % quote_err
            reasons.append(
                "主力净流入不满足，要求>=%s万，实际%s"
                % (_fmt_num(MIN_INFLOW_WAN, 0), detail)
            )
        else:
            reasons.append(
                "主力净流入不满足，要求>=%s万，实际%s万"
                % (_fmt_num(MIN_INFLOW_WAN, 0), _fmt_num(inflow_wan, 2))
            )
    if not cond_prior:
        thr_pct = float(bthr) * 100.0
        if not prior_rets:
            reasons.append(
                "前%d日无大涨不满足，要求涨幅均<%s%%，实际无前%d日涨幅数据"
                % (int(PRIOR_LOOKBACK), _fmt_num(thr_pct, 2), int(PRIOR_LOOKBACK))
            )
        else:
            mp = max_prior if max_prior is not None else max(prior_rets)
            reasons.append(
                "前%d日无大涨不满足，要求涨幅均<%s%%，实际最高%s%%"
                % (
                    int(PRIOR_LOOKBACK),
                    _fmt_num(thr_pct, 2),
                    _fmt_num(float(mp) * 100.0, 4),
                )
            )
    if not cond_ma:
        reasons.append(
            "价站上MA5且MA20不满足，要求价>MA5且>MA20，实际价=%s MA5=%s MA20=%s"
            % (_fmt_num(price, 4), _fmt_num(ma5, 4), _fmt_num(ma20, 4))
        )
    if not cond_not_lu:
        reasons.append(
            "当天未涨停过不满足，要求当天未触及涨停，实际涨幅=%s%% 最高价=%s 涨停价=%s"
            % (_fmt_num(pct, 4), _fmt_num(high_px, 4), _fmt_num(lu_px, 4))
        )
    return "；".join(reasons)


def select(stock_code, stock_name, sectors, daily_data, as_of_date, ctx):
    # 触发引擎加载东财热门诊断；净流入走盘中 bundle
    _ = (ctx or {}).get("em_board_hot")

    c6 = _code6(stock_code)
    bundle = _bundle(as_of_date)
    q = ((bundle or {}).get("quotes") or {}).get(c6) or {}
    pct = q.get("pct")
    price = q.get("price")
    inflow_wan = q.get("inflow_wan")
    inflow_ratio = q.get("inflow_ratio")
    inflow_pct_float = q.get("inflow_pct_of_float")
    name_em = q.get("name") or stock_name

    if pct is None or price is None:
        d_pct, d_price = _daily_pct_today(daily_data, as_of_date)
        if pct is None:
            pct = d_pct
        if price is None:
            price = d_price

    thr = _pct_threshold(stock_code)
    hard_ok = pct is not None and float(pct) >= float(thr)
    if not hard_ok:
        return False, {
            "热门模式": HOT_MODE,
            "当时涨跌幅": "" if pct is None else round(float(pct), 4),
            "涨幅门槛": thr,
            "_skip": "当时涨幅未达硬门槛(主板7%/其他13%)",
        }

    # 疑似除权：不进结果表（整表回测/因子筛选都不应带上）
    no_ex_div, ex_div_gap, ex_div_date = _prior_ex_div_gap(
        stock_code, name_em, daily_data, as_of_date, EX_DIV_LOOKBACK
    )
    if not no_ex_div:
        ds = ""
        if ex_div_date is not None:
            try:
                ds = ex_div_date.strftime("%Y-%m-%d")
            except Exception:
                ds = str(ex_div_date)
        gap_pct = "" if ex_div_gap is None else round(float(ex_div_gap) * 100.0, 2)
        return False, {
            "热门模式": HOT_MODE,
            "_skip": "近%d日疑似除权" % int(EX_DIV_LOOKBACK),
            "疑似除权缺口日": ds,
            "疑似除权开盘缺口%": gap_pct,
        }

    closes = _closes_list(daily_data, as_of_date, live_price=price)
    ma5 = _ma(closes, 5)
    ma10 = _ma(closes, 10)
    ma20 = _ma(closes, 20)
    gap = None
    if ma5 is not None and ma10 is not None:
        lo_ma = min(float(ma5), float(ma10))
        if lo_ma > 0:
            gap = abs(float(ma5) - float(ma10)) / lo_ma

    prior_rets = _prior_session_rets(daily_data, as_of_date, PRIOR_LOOKBACK)
    max_prior, max_prior_date = _prior_max_ret_with_date(
        daily_data, as_of_date, PRIOR_LOOKBACK
    )
    if max_prior is None and prior_rets:
        max_prior = max(prior_rets)
        max_prior_date = None
    bthr = _big_move_threshold(stock_code)
    no_big_move = bool(prior_rets) and all(
        not _is_big_move_ret(r, bthr) for r in prior_rets
    )

    board_ok, best_rk, best_name, best_kind, hit_str = _board_top_hit(
        stock_code, sectors, bundle
    )
    owned_board = _owned_board_extra_fields(stock_code, sectors, bundle)
    inflow_ok = inflow_wan is not None and float(inflow_wan) >= float(MIN_INFLOW_WAN)
    above_ma = (
        price is not None
        and ma5 is not None
        and ma20 is not None
        and float(price) > float(ma5)
        and float(price) > float(ma20)
    )
    not_touched_lu = not _touched_limit_up_today(
        pct, q, daily_data, stock_code, name_em, as_of_date
    )

    meet_all = (
        bool(board_ok)
        and bool(inflow_ok)
        and bool(no_big_move)
        and bool(above_ma)
        and bool(not_touched_lu)
    )
    hot = _hot_fields_from_ctx(stock_code, ctx)
    if q.get("float_mv_yi") is not None and hot.get("流通市值_亿") in ("", None):
        try:
            hot["流通市值_亿"] = round(float(q["float_mv_yi"]), 2)
        except (TypeError, ValueError):
            pass

    high_px, pre_close_px = _day_high_and_pre_close(q, daily_data, as_of_date)
    lu_px = _limit_up_price(pre_close_px, stock_code, name_em, as_of_date)
    fail_reasons = _fail_reasons_logic2(
        cond_board=bool(board_ok),
        cond_inflow=bool(inflow_ok),
        cond_prior=bool(no_big_move),
        cond_ma=bool(above_ma),
        cond_not_lu=bool(not_touched_lu),
        owned_board=owned_board,
        inflow_wan=inflow_wan,
        max_prior=max_prior,
        bthr=bthr,
        prior_rets=prior_rets,
        price=price,
        ma5=ma5,
        ma20=ma20,
        pct=pct,
        high_px=high_px,
        lu_px=lu_px,
        board_err=str((bundle or {}).get("board_err") or ""),
        quote_err=str((bundle or {}).get("quote_err") or ""),
    )

    extra = {
        "热门模式": HOT_MODE,
        "满足条件": bool(meet_all),
        "不满足的原因": fail_reasons,
        "前十个交易日最高涨幅": "" if max_prior is None else round(float(max_prior) * 100.0, 4),
        "前十个交易日最高涨幅日期": "" if max_prior_date is None else max_prior_date.strftime("%Y-%m-%d"),
        "当时涨跌幅": round(float(pct), 4),
        "当时最新价": "" if price is None else round(float(price), 4),
        "今日最高价": "" if high_px is None else round(float(high_px), 4),
        "涨停价": "" if lu_px is None else round(float(lu_px), 4),
        "涨幅门槛": thr,
        "条件_盘中涨幅达标": True,
        "条件_当天未涨停过": bool(not_touched_lu),
        "条件_非当时涨停": bool(not _is_limit_up_now(pct, stock_code, name_em, as_of_date)),
        "条件_行业或概念排名达标": bool(board_ok),
        "条件_行业前N": int(BOARD_TOP_N_INDUSTRY),
        "条件_概念前N": int(BOARD_TOP_N_CONCEPT),
        "条件_主力净流入>=5000万": bool(inflow_ok),
        "条件_前10日无大涨": bool(no_big_move),
        "条件_价站上MA5且MA20": bool(above_ma),
        "除权排查天数": int(EX_DIV_LOOKBACK),
        "除权缺口阈值加EPS": round(float(EX_DIV_EPS) * 100.0, 2),
        "前10日大涨阈值": round(float(bthr) * 100.0, 2),
        "涨停幅度阈值": _limit_up_pct(stock_code, name_em, as_of_date),
        "所属行业最高排名名次": owned_board["所属行业最高排名名次"],
        "所属行业最高排名名称": owned_board["所属行业最高排名名称"],
        "所属行业最高排名涨幅": owned_board["所属行业最高排名涨幅"],
        "所属行业最高排名净占比": owned_board["所属行业最高排名净占比"],
        "所属概念最高排名名次": owned_board["所属概念最高排名名次"],
        "所属概念最高排名名称": owned_board["所属概念最高排名名称"],
        "所属概念最高排名涨幅": owned_board["所属概念最高排名涨幅"],
        "所属概念最高排名净占比": owned_board["所属概念最高排名净占比"],
        "命中前8标签": hit_str,
        "最佳板块排名": "" if best_rk is None else int(best_rk),
        "最佳板块名称": best_name,
        "最佳板块类型": best_kind,
        "主力净流入_万元": "" if inflow_wan is None else round(float(inflow_wan), 2),
        "主力净流入-净占比": "" if inflow_ratio is None else round(float(inflow_ratio), 4),
        "净流入占流通%": "" if inflow_pct_float is None else round(float(inflow_pct_float), 4),
        "数据模式": str((bundle or {}).get("mode") or ""),
        "数据抓取时间": str((bundle or {}).get("fetched_at") or ""),
        "板块排名备注": str((bundle or {}).get("board_err") or ""),
        "行情备注": str((bundle or {}).get("quote_err") or ""),
        "MA5": "" if ma5 is None else round(ma5, 4),
        "MA10": "" if ma10 is None else round(ma10, 4),
        "MA20": "" if ma20 is None else round(ma20, 4),
        "均线差占比": "" if gap is None else round(gap, 6),
        "TOP_N": int(TOP_N),
        "BOARD_TOP_N_INDUSTRY": int(BOARD_TOP_N_INDUSTRY),
        "BOARD_TOP_N_CONCEPT": int(BOARD_TOP_N_CONCEPT),
        "BOARD_TOP_N": int(BOARD_TOP_N),
        "BOARD_SHOW_TOP_N": int(BOARD_SHOW_TOP_N),
        "MIN_INFLOW_WAN": float(MIN_INFLOW_WAN),
        "PRIOR_LOOKBACK": int(PRIOR_LOOKBACK),
        "EX_DIV_LOOKBACK": int(EX_DIV_LOOKBACK),
        "EX_DIV_EPS": float(EX_DIV_EPS),
        "MAIN_PCT_LO": float(MAIN_PCT_LO),
        "GROWTH_PCT_LO": float(GROWTH_PCT_LO),
        "ANY_TAG": bool(ANY_TAG),
        "REQUIRE_MIN_FLOAT_MV": bool(REQUIRE_MIN_FLOAT_MV),
        "REQUIRE_MA_GAP": bool(REQUIRE_MA_GAP),
        "REQUIRE_MA_LT_ALIGN": bool(REQUIRE_MA_LT_ALIGN),
        "REQUIRE_NO_RECENT_LU": bool(REQUIRE_NO_RECENT_LU),
        "APPLY_STOCK_FILTERS": bool(APPLY_STOCK_FILTERS),
        "股票名称_行情": name_em,
    }
    extra.update(hot)
    return True, extra
