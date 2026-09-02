# 马总短线选股逻辑
# 参考「马总选股逻辑-次日MA10」字段骨架；三点差异：
#   1) 硬条件：必须选股日当日涨停（L=D；非近10日回溯）
#   2) 软门槛：前 PRIOR_LOOKBACK=8 个交易日无大涨（非前10日）
#   3) 软门槛：不判断布林上轨
# 另：仅「满足条件」四项全真才写入结果表（不输出不满足行）。
# 硬过滤（不进结果表）：
#   - 选股日未涨停，或涨停后已触 MA10（L=D 时无可扫日，视为未触）
#   - 近 EX_DIV_LOOKBACK 个交易日（不含当日）出现疑似除权开盘缺口
# 满足条件（四项全真才入选；行情类字段一律相对涨停锚点日 L=选股日）：
#   1) 涨停日所属行业东财涨幅排名前 BOARD_TOP_N_INDUSTRY，或概念前 BOARD_TOP_N_CONCEPT
#   2) 涨停日主力资金净流入 >= 3000万
#   3) 涨停日前8个交易日：主板无涨幅>=5%；创业/科创/北交所无涨幅>=10%
#   4) 涨停日收盘价 > MA5 且 > MA20
# 「前八个交易日最高涨幅/日期」、近5/10/20日RS、除权排查、涨停次数等同理相对 L
# 另输出选股日对照列（后缀 _选股日）：收盘/MA/均线差/RS/净流入/行业概念排名/市值
# 依赖引擎 ctx["em_board_hot"]（触发引擎按选股日预载；本规则展示/门槛改用涨停锚点日热门与净流入）
# 引擎：关闭热门池收窄，全市场扫描
USE_EM_CANDIDATE_POOL = False
TOP_N = 50
RS_TOP_K = 50
RS_LO = 1
RS_HI = 50
RS_TOP_FRAC_NUM = 1
RS_TOP_FRAC_DEN = 2
RS_LOOKBACK = 10
RS_LOOKBACK_5 = 5
RS_LOOKBACK_20 = 20
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
HOT_MODE = "ma_zong_short_term"
N = 20
BOARD_TOP_N_INDUSTRY = 40  # 满足条件：所属行业东财涨幅排名门槛
BOARD_TOP_N_CONCEPT = 10  # 满足条件：所属概念东财涨幅排名门槛
BOARD_TOP_N = BOARD_TOP_N_INDUSTRY  # 兼容旧字段（取行业门槛）
BOARD_SHOW_TOP_N = 30  # 表格展示名次上限（可大于入选门槛）
MIN_INFLOW_WAN = 3000.0
PRIOR_LOOKBACK = 8  # 前 N 日无大涨（相对涨停日）
LU_LOOKBACK = 1  # 仅选股日当日涨停
MA_TOUCH_PERIOD = 10  # 触线均线周期（早盘「10日」用近 9 收）
EX_DIV_LOOKBACK = 20  # 近 N 交易日（不含当日）排查疑似除权
EX_DIV_EPS = 0.005  # 超出涨跌停幅度的容差（0.5%）

_DAY_CACHE = {}
_HOT_DAY_CACHE = {}
_STOCK_TAG_CACHE = {"loaded": False, "code_to_tags": {}}


def _elig_hi_for_kind(kind):
    k = str(kind or "").strip().lower()
    if k in ("concept", "概念"):
        return int(ELIG_HI_CONCEPT)
    return int(ELIG_HI_SECTOR)


def _limit_ratio(stock_code, stock_name, as_of_date=None):
    from datetime import date as _date
    name = str(stock_name or "").upper()
    code = str(stock_code or "").strip()
    if code.startswith(("300", "301", "688", "689")):
        return 0.20
    if code.startswith(("8", "4", "920")):
        return 0.30
    if "ST" in name:
        ref = as_of_date if as_of_date is not None else _date.today()
        if not isinstance(ref, _date):
            try:
                ref = ref.date()
            except Exception:
                try:
                    ref = ref.to_pydatetime().date()
                except Exception:
                    ref = _date.today()
        if ref >= _date(2026, 7, 6):
            return 0.10
        return 0.05
    return 0.10


def _is_growth_board(stock_code):
    code = str(stock_code or "").strip()
    if "." in code:
        code = code.split(".", 1)[0]
    return code.startswith(("300", "301", "688", "689", "8", "4", "920"))


def _big_move_threshold(stock_code):
    return 0.10 if _is_growth_board(stock_code) else 0.05


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


def _ma(closes, n):
    if closes is None or len(closes) < n:
        return None
    return float(sum(closes[-n:])) / float(n)


def _boll_upper(closes, period=20, k=2.0):
    """布林上轨 = MA(period) + k * STD(period)；样本标准差（ddof=1）。不足 period 根返回 None。"""
    p = max(2, int(period or 20))
    if closes is None or len(closes) < p:
        return None
    try:
        window = [float(x) for x in closes[-p:]]
    except (TypeError, ValueError):
        return None
    if any(x != x or x <= 0 for x in window):
        return None
    mean = sum(window) / float(p)
    var = sum((x - mean) ** 2 for x in window) / float(p - 1)
    if var != var or var < 0:
        return None
    return mean + float(k) * (var ** 0.5)


def _absolute_rs(closes, lookback):
    """个股近 lookback 日绝对 RS；(close_D - close_{D-lookback}) / close_{D-lookback}。"""
    lb = max(1, int(lookback or 1))
    need = lb + 1
    if closes is None or len(closes) < need:
        return None
    try:
        c_d = float(closes[-1])
        c_prev = float(closes[-need])
    except (TypeError, ValueError, IndexError):
        return None
    if c_d != c_d or c_prev != c_prev or c_prev <= 0 or c_d <= 0:
        return None
    return (c_d - c_prev) / c_prev


def _closes_through(daily_data, as_of_date):
    if daily_data is None:
        return None
    try:
        if len(daily_data) == 0:
            return None
    except Exception:
        return None
    as_d = _as_date(as_of_date)
    rows = []
    for _, r in daily_data.iterrows():
        dd = _as_date(r.get("_d") or r.get("date"))
        if dd is None:
            continue
        if as_d is not None and dd > as_d:
            continue
        try:
            c = float(r.get("close"))
        except (TypeError, ValueError):
            continue
        if c == c and c > 0:
            rows.append(c)
    return rows


def _is_limit_up_on_row(prev_close, close_price, limit_ratio):
    if prev_close is None or prev_close <= 0:
        return False, 0.0
    limit_up_price = round(float(prev_close) * (1.0 + limit_ratio), 2)
    price_diff = abs(float(close_price) - limit_up_price)
    inc = (float(close_price) - float(prev_close)) / float(prev_close)
    ok = (price_diff < 0.02) or (inc >= limit_ratio * 0.99)
    return ok, limit_up_price


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


def _recent_lu_stats(stock_code, stock_name, daily_data, as_of_date, lookback):
    """近 lookback 个交易日涨停次数（不含选股日 as_of），及最近一次距 as_of 的交易日偏移。

    偏移：1=上一交易日，2=再上一交易日，…；窗口内无涨停则返回 ""。
    """
    dd = _sorted_daily(daily_data, as_of_date)
    if dd is None:
        return 0, ""
    as_d = _as_date(as_of_date)
    dcol = "_d" if "_d" in dd.columns else "date"
    if as_d is not None:
        if dcol == "_d":
            prev_dd = dd[dd[dcol] < as_d]
        else:
            prev_dd = dd[dd[dcol].map(_as_date) < as_d]
    else:
        # 无 as_of 时退化为去掉最后一根，避免把“当日”算进窗口
        prev_dd = dd.iloc[:-1] if len(dd) > 0 else dd
    if prev_dd is None or getattr(prev_dd, "empty", True):
        return 0, ""
    dates = list(
        (prev_dd["_d"] if "_d" in prev_dd.columns else prev_dd["date"].map(_as_date)).tolist()
    )
    if not dates:
        return 0, ""
    lb = max(1, int(lookback))
    window = dates[-lb:] if len(dates) >= lb else dates
    lu_offsets = []
    for i, trade_date in enumerate(window):
        sub = prev_dd[
            (prev_dd["_d"] if "_d" in prev_dd.columns else prev_dd["date"].map(_as_date))
            == trade_date
        ]
        if sub.empty:
            continue
        # 昨收仍从含当日的完整序列取，保证窗口首日也能判涨停
        prev = dd[
            (dd["_d"] if "_d" in dd.columns else dd["date"].map(_as_date)) < trade_date
        ]
        if prev.empty:
            continue
        prev_close = float(prev.iloc[-1]["close"])
        close_price = float(sub.iloc[-1]["close"])
        limit_ratio = _limit_ratio(stock_code, stock_name, trade_date)
        ok, _ = _is_limit_up_on_row(prev_close, close_price, limit_ratio)
        if ok:
            # 窗口末日=上一交易日 → offset 1
            lu_offsets.append(len(window) - i)
    count = len(lu_offsets)
    days_ago = min(lu_offsets) if lu_offsets else ""
    return count, days_ago


def _today_limit_up(stock_code, stock_name, daily_data, as_of_date):
    dd = _sorted_daily(daily_data, as_of_date)
    if dd is None:
        return False, None, None
    as_d = _as_date(as_of_date)
    row = None
    if as_d is not None:
        for _, r in dd.iloc[::-1].iterrows():
            if _as_date(r.get("_d") or r.get("date")) == as_d:
                row = r
                break
    if row is None:
        row = dd.iloc[-1]
        as_d = _as_date(row.get("_d") or row.get("date"))
    prev = dd[(dd["_d"] if "_d" in dd.columns else dd["date"].map(_as_date)) < _as_date(row.get("_d") or row.get("date"))]
    if prev.empty:
        return False, None, None
    prev_close = float(prev.iloc[-1]["close"])
    close_price = float(row["close"])
    lr = _limit_ratio(stock_code, stock_name, as_d)
    ok, _ = _is_limit_up_on_row(prev_close, close_price, lr)
    return bool(ok), close_price, prev_close


def _asof_close(daily_data, as_of_date):
    """选股日收盘价；无当日 K 则取截止 as_of 的最后一根。"""
    dd = _sorted_daily(daily_data, as_of_date)
    if dd is None:
        return None
    as_d = _as_date(as_of_date)
    if as_d is not None:
        for _, r in dd.iloc[::-1].iterrows():
            if _as_date(r.get("_d") or r.get("date")) == as_d:
                try:
                    return float(r["close"])
                except (TypeError, ValueError):
                    return None
    try:
        return float(dd.iloc[-1]["close"])
    except (TypeError, ValueError, IndexError):
        return None


def _latest_lu_in_lookback(stock_code, stock_name, daily_data, as_of_date, lookback):
    """近 lookback 个交易日（含 as_of）最近一次涨停日。

    返回 (lu_date|None, days_ago: 0=当日, 1=上一交易日, …)。
    """
    dd = _sorted_daily(daily_data, as_of_date)
    if dd is None:
        return None, ""
    as_d = _as_date(as_of_date)
    dcol = "_d" if "_d" in dd.columns else "date"
    dates = list((dd["_d"] if "_d" in dd.columns else dd[dcol].map(_as_date)).tolist())
    if not dates:
        return None, ""
    lb = max(1, int(lookback))
    window = dates[-lb:] if len(dates) >= lb else list(dates)
    latest = None
    days_ago = ""
    for i, trade_date in enumerate(window):
        sub = dd[
            (dd["_d"] if "_d" in dd.columns else dd["date"].map(_as_date)) == trade_date
        ]
        if sub.empty:
            continue
        prev = dd[
            (dd["_d"] if "_d" in dd.columns else dd["date"].map(_as_date)) < trade_date
        ]
        if prev.empty:
            continue
        try:
            prev_close = float(prev.iloc[-1]["close"])
            close_price = float(sub.iloc[-1]["close"])
        except (TypeError, ValueError):
            continue
        limit_ratio = _limit_ratio(stock_code, stock_name, trade_date)
        ok, _ = _is_limit_up_on_row(prev_close, close_price, limit_ratio)
        if ok:
            latest = trade_date
            days_ago = len(window) - 1 - i  # 窗口末日=as_of → 0
    return latest, days_ago if latest is not None else ""


def _touched_ma_after_lu(daily_data, lu_date, as_of_date, ma_period=10):
    """涨停日次日～as_of（含）是否已有 low<=早盘「N日」触发价。

    与 utils.first_ma_touch / backtest 早盘「10日」一致：触发价=不含当日的近 (N-1) 根收盘均。
    返回 (touched, touch_date|None)。L=as_of 时无可扫日 → (False, None)。
    """
    dd = _sorted_daily(daily_data, as_of_date)
    if dd is None:
        return False, None
    lu_d = _as_date(lu_date)
    as_d = _as_date(as_of_date)
    if lu_d is None or as_d is None:
        return False, None
    if "low" not in dd.columns or "close" not in dd.columns:
        return False, None
    period = max(2, int(ma_period or 10))
    need_prior = period - 1
    dates = list(dd["_d"].tolist() if "_d" in dd.columns else dd["date"].map(_as_date))
    try:
        closes = [float(x) for x in dd["close"].tolist()]
        lows = [float(x) for x in dd["low"].tolist()]
    except (TypeError, ValueError):
        return False, None
    for i, d in enumerate(dates):
        if d is None or d <= lu_d or d > as_d:
            continue
        if i < need_prior:
            continue
        prior = closes[i - need_prior : i]
        if len(prior) < need_prior:
            continue
        try:
            trig = round(float(sum(prior)) / float(need_prior), 2)
            low_v = float(lows[i])
        except (TypeError, ValueError):
            continue
        if trig <= 0 or low_v != low_v or low_v <= 0:
            continue
        if low_v <= trig + 1e-9:
            return True, d
    return False, None


def _prior_session_rets(daily_data, anchor_date, lookback):
    """不含锚点日的前 lookback 个交易日涨幅列表（小数）。

    锚点一般为涨停日 L：只取 L 之前最后 lookback+1 根K线再算涨幅，避免把涨停日算进窗口。
    """
    as_d = _as_date(anchor_date)
    if as_d is None:
        return []
    dd = _sorted_daily(daily_data, anchor_date)
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


def _prior_max_ret_with_date(daily_data, anchor_date, lookback):
    """(最大涨幅小数, 日期)；窗口为锚点日之前 lookback 个交易日（不含锚点日）。"""
    as_d = _as_date(anchor_date)
    if as_d is None:
        return None, None
    dd = _sorted_daily(daily_data, anchor_date)
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

    判定：某日 开盘/昨收 - 1 < -(该日涨跌停幅度 + EX_DIV_EPS)。
    普通跌停开盘约等于一个跌停幅；再深通常为除权/特殊复牌。

    返回 (无缺口合格?, 最深缺口小数或None, 缺口日或None)。
    """
    as_d = _as_date(as_of_date)
    if as_d is None:
        return True, None, None
    dd = _sorted_daily(daily_data, as_of_date)
    if dd is None:
        return True, None, None
    if "open" not in dd.columns:
        return True, None, None
    dcol = "_d" if "_d" in dd.columns else "date"
    if dcol == "date":
        prev = dd[dd[dcol].map(_as_date) < as_d]
    else:
        prev = dd[dd[dcol] < as_d]
    if prev is None or getattr(prev, "empty", True) or len(prev) < 2:
        return True, None, None
    lb = max(1, int(lookback))
    # 多取 1 根以便算窗口首日的昨收
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
        lr = float(_limit_ratio(stock_code, stock_name, d))
        thr = -(lr + float(EX_DIV_EPS))
        if gap < thr:
            if worst is None or gap < worst:
                worst = gap
                worst_d = d
    ok = worst is None
    return bool(ok), worst, worst_d


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


def _ymd(as_of_date):
    d = _as_date(as_of_date)
    if d is None:
        return ""
    try:
        return d.strftime("%Y%m%d")
    except Exception:
        s = str(as_of_date)
        digits = "".join(ch for ch in s if ch.isdigit())
        return digits[:8] if len(digits) >= 8 else ""


def _day_bundle(as_of_date):
    key = _ymd(as_of_date) or str(as_of_date)
    if key in _DAY_CACHE:
        return _DAY_CACHE[key]
    bundle = {
        "ind_rank": {},
        "con_rank": {},
        "ind_chg": {},
        "con_chg": {},
        "ind_flow_ratio": {},
        "con_flow_ratio": {},
        "inflow_wan": {},
        "inflow_ratio": {},
        "inflow_pct_of_float": {},
        "float_mv_yi": {},
        "code_best_industry": {},
        "code_best_concept": {},
        "code_industry_hits": {},
        "code_concept_hits": {},
        "board_err": "",
        "inflow_err": "",
    }
    try:
        from utils.eastmoney_board_rank_ctx import board_rank_csv_paths, _load_rank_chg_maps
        paths = board_rank_csv_paths(as_of_date)
        ind_rank, ind_chg = _load_rank_chg_maps(paths.get("industry") or "")
        con_rank, con_chg = _load_rank_chg_maps(paths.get("concept") or "")
        bundle["ind_rank"] = ind_rank
        bundle["con_rank"] = con_rank
        bundle["ind_chg"] = ind_chg
        bundle["con_chg"] = con_chg
        if not bundle["ind_rank"] and not bundle["con_rank"]:
            bundle["board_err"] = "无东财行业/概念涨幅榜"
    except Exception as e:
        bundle["board_err"] = "加载板块排名失败:%s" % e

    try:
        from utils.eastmoney_board_rank_ctx import (
            board_fund_flow_csv_paths,
            _load_fund_flow_net_ratio_map,
        )
        fpaths = board_fund_flow_csv_paths(as_of_date)
        bundle["ind_flow_ratio"] = _load_fund_flow_net_ratio_map(fpaths.get("industry") or "")
        bundle["con_flow_ratio"] = _load_fund_flow_net_ratio_map(fpaths.get("concept") or "")
    except Exception as e:
        msg = "加载板块资金流失败:%s" % e
        bundle["board_err"] = ("%s; %s" % (bundle.get("board_err") or "", msg)).strip("; ")

    # 成分归属反查：每只股票的最高热门行业 / 最高热门概念（及全量命中列表）
    try:
        from utils.eastmoney_board_rank_ctx import build_code_owned_board_rank_maps

        owned_maps = build_code_owned_board_rank_maps(as_of_date)
        if isinstance(owned_maps, dict):
            bundle["code_best_industry"] = owned_maps.get("code_best_industry") or {}
            bundle["code_best_concept"] = owned_maps.get("code_best_concept") or {}
            bundle["code_industry_hits"] = owned_maps.get("code_industry_hits") or {}
            bundle["code_concept_hits"] = owned_maps.get("code_concept_hits") or {}
            if owned_maps.get("error") and not bundle.get("board_err"):
                bundle["board_err"] = str(owned_maps.get("error") or "")
    except Exception as e:
        msg = "成分归属反查失败:%s" % e
        bundle["board_err"] = ("%s; %s" % (bundle.get("board_err") or "", msg)).strip("; ")

    try:
        from utils.main_force_inflow_path import resolve_flow_csv_path
        from utils.main_force_inflow_rank import parse_inflow_to_yuan
        import pandas as pd
        import os
        import pathlib
        ymd = _ymd(as_of_date)
        path = None
        hist_candidates = []
        # 优先：与 resolve 同模块定位仓库根，避免选股进程 cwd 不在项目根时找不到 CSV
        try:
            import utils.main_force_inflow_path as _mfp

            _root = os.path.dirname(os.path.dirname(os.path.abspath(_mfp.__file__)))
            hist_candidates.append(os.path.join(_root, "history_data"))
        except Exception:
            pass
        try:
            from utils.eastmoney_board_rank_ctx import _project_root

            hist_candidates.append(os.path.join(_project_root(), "history_data"))
        except Exception:
            pass
        hist_candidates.append("history_data")
        hist_candidates.append(str(pathlib.Path.cwd() / "history_data"))
        # 去重且保序
        _seen_hist = set()
        _uniq = []
        for h in hist_candidates:
            h = os.path.normpath(str(h))
            if h in _seen_hist:
                continue
            _seen_hist.add(h)
            _uniq.append(h)
        hist_candidates = _uniq
        if ymd:
            for hist in hist_candidates:
                path = resolve_flow_csv_path(ymd, hist)
                if path:
                    break
        if not path:
            bundle["inflow_err"] = "无主力净流入CSV"
        else:
            raw = None
            for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
                try:
                    raw = pd.read_csv(path, encoding=enc)
                    break
                except Exception:
                    continue
            if raw is None or raw.empty:
                bundle["inflow_err"] = "主力净流入CSV读失败"
            else:
                raw.columns = [str(c).strip() for c in raw.columns]
                code_col = "代码" if "代码" in raw.columns else None
                if code_col is None:
                    for c in raw.columns:
                        if "代码" in str(c):
                            code_col = c
                            break
                inflow_col = None
                for c in raw.columns:
                    cs = str(c)
                    if "主力净流入" in cs and "净额" in cs:
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
                mp = {}
                mp_ratio = {}
                mp_pct_float = {}
                mp_mv_yi = {}
                if code_col and inflow_col:
                    for _, row in raw.iterrows():
                        c6 = _code6(row.get(code_col))
                        if not c6:
                            continue
                        yuan = parse_inflow_to_yuan(row.get(inflow_col))
                        if yuan is not None:
                            mp[c6] = float(yuan) / 1e4  # 万元
                        if mv_col is not None and c6 not in mp_mv_yi:
                            try:
                                mv_yuan = parse_inflow_to_yuan(row.get(mv_col))
                                if mv_yuan and float(mv_yuan) > 0:
                                    mp_mv_yi[c6] = float(mv_yuan) / 1e8  # 亿
                            except (TypeError, ValueError):
                                pass
                        if ratio_col is not None and c6 not in mp_ratio:
                            try:
                                rv = row.get(ratio_col)
                                if rv not in (None, "") and str(rv).strip() not in (
                                    "--",
                                    "-",
                                    "nan",
                                    "None",
                                ):
                                    mp_ratio[c6] = float(
                                        str(rv).replace("%", "").replace(",", "")
                                    )
                            except (TypeError, ValueError):
                                pass
                        pf = None
                        if pct_float_col is not None:
                            try:
                                rv = row.get(pct_float_col)
                                if rv not in (None, "") and str(rv).strip() not in (
                                    "--",
                                    "-",
                                    "nan",
                                    "None",
                                ):
                                    pf = float(str(rv).replace("%", "").replace(",", ""))
                            except (TypeError, ValueError):
                                pf = None
                        if pf is None and yuan is not None and mv_col is not None:
                            try:
                                mv_yuan = parse_inflow_to_yuan(row.get(mv_col))
                                if mv_yuan and float(mv_yuan) > 0:
                                    pf = float(yuan) / float(mv_yuan) * 100.0
                            except (TypeError, ValueError):
                                pf = None
                        if pf is not None:
                            mp_pct_float[c6] = float(pf)
                bundle["inflow_wan"] = mp
                bundle["inflow_ratio"] = mp_ratio
                bundle["inflow_pct_of_float"] = mp_pct_float
                bundle["float_mv_yi"] = mp_mv_yi
                if not mp:
                    bundle["inflow_err"] = "主力净流入表无有效金额"
    except Exception as e:
        bundle["inflow_err"] = "加载主力净流入失败:%s" % e

    _DAY_CACHE[key] = bundle
    return bundle


def _em_hot_for_day(as_of_date):
    """按锚点日缓存东财热门上下文（勿用选股日 ctx 顶替）。"""
    key = _ymd(as_of_date) or str(as_of_date)
    if key in _HOT_DAY_CACHE:
        return _HOT_DAY_CACHE[key]
    em = {}
    try:
        from utils.eastmoney_board_rank_ctx import load_em_board_hot_map

        em = load_em_board_hot_map(
            as_of_date,
            top_n=int(TOP_N),
            rs_top_k=int(RS_TOP_K),
            rs_lookback=int(RS_LOOKBACK),
            min_members=int(MIN_MEMBERS),
            arms="today",
            elig_lo=int(ELIG_LO),
            elig_hi=int(ELIG_HI),
        )
        if not isinstance(em, dict):
            em = {}
    except Exception:
        em = {}
    _HOT_DAY_CACHE[key] = em
    return em


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
        "近5日RS": "",
        "近10日RS": "",
        "近20日RS": "",
        "命中今日热门标签数": "",
        "流通市值_亿": "",
        "东财榜日期D": "",
        "东财榜日期D-1": "",
    }


def _hot_fields_from_ctx(stock_code, ctx):
    out = _empty_hot_fields()
    em = (ctx or {}).get("em_board_hot") or {}
    if not isinstance(em, dict) or not em:
        return out, None
    out["东财榜日期D"] = str(em.get("as_of") or "")
    out["东财榜日期D-1"] = str(em.get("prev_date") or "")
    c6 = _code6(stock_code)
    hits = em.get("today_code_hits") or {}
    hit = hits.get(c6) if isinstance(hits, dict) else None
    mv_map = em.get("float_mv_yi") or {}
    mv = None
    if isinstance(mv_map, dict):
        try:
            raw = mv_map.get(c6)
            mv = float(raw) if raw is not None and raw != "" else None
        except (TypeError, ValueError):
            mv = None
    out["流通市值_亿"] = "" if mv is None else round(mv, 2)
    if not isinstance(hit, dict):
        return out, mv

    def _as_int(v, default=""):
        try:
            if v is None or v == "":
                return default
            return int(v)
        except (TypeError, ValueError):
            return default

    elig = _as_int(hit.get("合格榜内序位"))
    rs_in_tag = _as_int(hit.get("合格榜标签内RS排名"))
    tag_rs_n = _as_int(hit.get("合格榜标签RS样本数"))
    rs_cut = ""
    try:
        if tag_rs_n not in ("", None) and int(tag_rs_n) > 0 and RS_HI is not None:
            den = int(RS_TOP_FRAC_DEN) if int(RS_TOP_FRAC_DEN) > 0 else 2
            num = int(RS_TOP_FRAC_NUM) if int(RS_TOP_FRAC_NUM) > 0 else 1
            frac_cut = max(1, (int(tag_rs_n) * num + den - 1) // den)
            rs_cut = min(int(RS_HI), frac_cut)
    except Exception:
        rs_cut = ""

    tag = hit.get("合格榜对应标签", "")
    kind = hit.get("合格榜标签类型", "")
    em_rank = hit.get("合格榜标签东财排名", "")
    out.update({
        "合格榜内序位": elig,
        "合格榜对应标签": tag,
        "合格榜标签类型": kind,
        "合格榜标签东财排名": em_rank,
        "合格榜标签内RS排名": rs_in_tag,
        "合格榜标签RS样本数": tag_rs_n,
        "合格榜标签RS截断": rs_cut,
        "选出标签": tag,
        "选出标签类型": kind,
        "选出标签合格榜内序位": elig,
        "选出标签东财排名": em_rank,
        "选出标签内RS排名": rs_in_tag,
        "选出标签RS样本数": tag_rs_n,
        "选出标签RS截断": rs_cut,
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
    return out, mv


def _hot_fields_for_anchor(stock_code, anchor_date):
    """热门诊断列相对涨停锚点日；按日缓存 load_em_board_hot_map。"""
    em = _em_hot_for_day(anchor_date)
    return _hot_fields_from_ctx(stock_code, {"em_board_hot": em})


def _sel_day_contrast_fields(stock_code, sectors, daily_data, as_of_date):
    """选股日 D 对照列（与涨停日 L 字段对照；L=D 时数值相同）。"""
    out = {
        "收盘价_选股日": "",
        "MA5_选股日": "",
        "MA10_选股日": "",
        "MA20_选股日": "",
        "均线差占比_选股日": "",
        "近5日RS_选股日": "",
        "近10日RS_选股日": "",
        "近20日RS_选股日": "",
        "主力净流入_万元_选股日": "",
        "主力净流入_选股日": "",
        "主力净流入-净占比_选股日": "",
        "所属行业最高排名名次_选股日": "",
        "所属行业最高排名名称_选股日": "",
        "所属概念最高排名名次_选股日": "",
        "所属概念最高排名名称_选股日": "",
        "流通市值_亿_选股日": "",
    }
    as_d = _as_date(as_of_date)
    if as_d is None:
        return out

    close_d = _asof_close(daily_data, as_d)
    closes_d = _closes_through(daily_data, as_d)
    ma5_d = _ma(closes_d, 5)
    ma10_d = _ma(closes_d, 10)
    ma20_d = _ma(closes_d, 20)
    gap_d = None
    if ma5_d is not None and ma10_d is not None:
        lo_ma = min(float(ma5_d), float(ma10_d))
        if lo_ma > 0:
            gap_d = abs(float(ma5_d) - float(ma10_d)) / lo_ma
    if close_d is not None:
        out["收盘价_选股日"] = round(float(close_d), 4)
    if ma5_d is not None:
        out["MA5_选股日"] = round(float(ma5_d), 4)
    if ma10_d is not None:
        out["MA10_选股日"] = round(float(ma10_d), 4)
    if ma20_d is not None:
        out["MA20_选股日"] = round(float(ma20_d), 4)
    if gap_d is not None:
        out["均线差占比_选股日"] = round(float(gap_d), 6)
    for key, lb in (
        ("近5日RS_选股日", RS_LOOKBACK_5),
        ("近10日RS_选股日", RS_LOOKBACK),
        ("近20日RS_选股日", RS_LOOKBACK_20),
    ):
        rs = _absolute_rs(closes_d, lb)
        if rs is not None:
            out[key] = round(float(rs), 6)

    c6 = _code6(stock_code)
    bundle_d = _day_bundle(as_d)
    inflow_wan_d = (bundle_d.get("inflow_wan") or {}).get(c6)
    inflow_ratio_d = (bundle_d.get("inflow_ratio") or {}).get(c6)
    if inflow_wan_d is not None:
        out["主力净流入_万元_选股日"] = round(float(inflow_wan_d), 2)
        out["主力净流入_选股日"] = "%s万" % round(float(inflow_wan_d), 2)
    if inflow_ratio_d is not None:
        out["主力净流入-净占比_选股日"] = round(float(inflow_ratio_d), 4)
    owned_d = _owned_board_extra_fields(stock_code, sectors, bundle_d)
    out["所属行业最高排名名次_选股日"] = owned_d.get("所属行业最高排名名次") or ""
    out["所属行业最高排名名称_选股日"] = owned_d.get("所属行业最高排名名称") or ""
    out["所属概念最高排名名次_选股日"] = owned_d.get("所属概念最高排名名次") or ""
    out["所属概念最高排名名称_选股日"] = owned_d.get("所属概念最高排名名称") or ""
    mv_d = (bundle_d.get("float_mv_yi") or {}).get(c6)
    if mv_d is None:
        _, mv_hot_d = _hot_fields_for_anchor(stock_code, as_d)
        mv_d = mv_hot_d
    if mv_d is not None:
        out["流通市值_亿_选股日"] = round(float(mv_d), 2)
    return out


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


def _fmt_hits_detail(hits):
    """[(rank, name, chg), ...] → '名称#名次;...'（全量列出）。"""
    if not hits:
        return ""
    parts = []
    for item in hits:
        try:
            rk, nm = int(item[0]), str(item[1] or "").strip()
        except Exception:
            continue
        if not nm or rk < 1:
            continue
        parts.append("%s#%d" % (nm, rk))
    return ";".join(parts)


def _owned_board_extra_fields(stock_code, sectors, bundle):
    """每只票的最高热门行业 / 最高热门概念（优先成分归属反查，回退标签精确撞榜）。"""
    c6 = _code6(stock_code)
    best_ind = ((bundle or {}).get("code_best_industry") or {}).get(c6) or {}
    best_con = ((bundle or {}).get("code_best_concept") or {}).get(c6) or {}
    ind_hits = ((bundle or {}).get("code_industry_hits") or {}).get(c6) or []
    con_hits = ((bundle or {}).get("code_concept_hits") or {}).get(c6) or []

    ind_rk = best_ind.get("rank") if isinstance(best_ind, dict) else None
    ind_name = (best_ind.get("name") if isinstance(best_ind, dict) else None) or ""
    ind_chg = best_ind.get("chg") if isinstance(best_ind, dict) else None
    ind_ratio = best_ind.get("flow_ratio") if isinstance(best_ind, dict) else None

    con_rk = best_con.get("rank") if isinstance(best_con, dict) else None
    con_name = (best_con.get("name") if isinstance(best_con, dict) else None) or ""
    con_chg = best_con.get("chg") if isinstance(best_con, dict) else None
    con_ratio = best_con.get("flow_ratio") if isinstance(best_con, dict) else None

    # 回退：旧逻辑（个股标签名精确匹配东财榜）
    if ind_rk is None or con_rk is None:
        tags = _stock_tags(stock_code, sectors)
        if ind_rk is None:
            ir, iname, ichg = _best_owned_board(
                tags, (bundle or {}).get("ind_rank") or {}, (bundle or {}).get("ind_chg") or {}
            )
            ind_rk, ind_name, ind_chg = ir, iname or "", ichg
            if ind_name:
                ind_fr = (bundle or {}).get("ind_flow_ratio") or {}
                ind_ratio = ind_fr.get(ind_name)
        if con_rk is None:
            cr, cname, cchg = _best_owned_board(
                tags, (bundle or {}).get("con_rank") or {}, (bundle or {}).get("con_chg") or {}
            )
            con_rk, con_name, con_chg = cr, cname or "", cchg
            if con_name:
                con_fr = (bundle or {}).get("con_flow_ratio") or {}
                con_ratio = con_fr.get(con_name)

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
        "所属行业排名明细": _fmt_hits_detail(ind_hits),
        "所属概念排名明细": _fmt_hits_detail(con_hits),
    }


def _board_top_hit(stock_code, sectors, as_of_date):
    bundle = _day_bundle(as_of_date)
    c6 = _code6(stock_code)
    ind_hits = list((bundle.get("code_industry_hits") or {}).get(c6) or [])
    con_hits = list((bundle.get("code_concept_hits") or {}).get(c6) or [])
    # 回退：无成分反查时仍用标签精确撞榜
    if not ind_hits and not con_hits:
        ind_rank = bundle.get("ind_rank") or {}
        con_rank = bundle.get("con_rank") or {}
        tags = _stock_tags(stock_code, sectors)
        for t in tags:
            if t in ind_rank:
                try:
                    ind_hits.append((int(ind_rank[t]), t, (bundle.get("ind_chg") or {}).get(t)))
                except Exception:
                    pass
            if t in con_rank:
                try:
                    con_hits.append((int(con_rank[t]), t, (bundle.get("con_chg") or {}).get(t)))
                except Exception:
                    pass
        ind_hits.sort(key=lambda x: (int(x[0]), str(x[1])))
        con_hits.sort(key=lambda x: (int(x[0]), str(x[1])))

    best_rank = None
    best_name = ""
    best_kind = ""
    show_hits = []
    all_hits = []
    cond_ok = False
    show_n = int(BOARD_SHOW_TOP_N)
    cond_n_ind = int(BOARD_TOP_N_INDUSTRY)
    cond_n_con = int(BOARD_TOP_N_CONCEPT)

    def _consider(tag, rk, kind):
        nonlocal best_rank, best_name, best_kind, cond_ok
        if rk is None:
            return
        try:
            rki = int(rk)
        except Exception:
            return
        if rki < 1:
            return
        all_hits.append((tag, rki, kind))
        if best_rank is None or rki < best_rank:
            best_rank, best_name, best_kind = rki, tag, kind
        if rki <= show_n:
            show_hits.append((tag, rki, kind))
        if kind == "行业" and rki <= cond_n_ind:
            cond_ok = True
        elif kind == "概念" and rki <= cond_n_con:
            cond_ok = True

    for rk, name, _chg in ind_hits:
        _consider(name, rk, "行业")
    for rk, name, _chg in con_hits:
        _consider(name, rk, "概念")

    # 全量列出所属行业+概念（按名次）；不再截断到 12 条，便于后续分位/排查
    src = all_hits if all_hits else show_hits
    hit_str = ";".join(
        "%s(%s#%d)" % (n, k, r) for n, r, k in sorted(src, key=lambda x: (x[1], x[2], x[0]))
    )
    return cond_ok, best_rank, best_name, best_kind, hit_str, bundle.get("board_err") or ""

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


def _fail_reasons_logic1(
    *,
    cond_board,
    cond_inflow,
    cond_prior,
    cond_ma,
    owned_board,
    inflow_wan,
    max_prior,
    thr,
    prior_rets,
    close_price,
    ma5,
    ma20,
    board_err,
    inflow_err,
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
            if inflow_err:
                detail += "(%s)" % inflow_err
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
        thr_pct = float(thr) * 100.0
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
            "收盘站上MA5且MA20不满足，要求收盘>MA5且>MA20，实际收盘=%s MA5=%s MA20=%s"
            % (_fmt_num(close_price, 4), _fmt_num(ma5, 4), _fmt_num(ma20, 4))
        )
    return "；".join(reasons)


def select(stock_code, stock_name, sectors, daily_data, as_of_date, ctx):
    # 触发引擎加载东财热门 / 净流入上下文（热门字段展示用；入选不依赖）
    _ = (ctx or {}).get("em_board_hot")

    lu_date, lu_days_ago = _latest_lu_in_lookback(
        stock_code, stock_name, daily_data, as_of_date, LU_LOOKBACK
    )
    if lu_date is None:
        return False, {"热门模式": HOT_MODE, "_skip": "选股日未涨停"}

    as_d = _as_date(as_of_date)
    is_lu_today = bool(as_d is not None and lu_date == as_d)
    if not is_lu_today:
        return False, {"热门模式": HOT_MODE, "_skip": "选股日未涨停"}

    touched, touch_date = _touched_ma_after_lu(
        daily_data, lu_date, as_of_date, MA_TOUCH_PERIOD
    )
    if touched:
        ts = ""
        if touch_date is not None:
            try:
                ts = touch_date.strftime("%Y-%m-%d")
            except Exception:
                ts = str(touch_date)
        return False, {
            "热门模式": HOT_MODE,
            "_skip": "涨停后已触MA10",
            "涨停锚点日": lu_date.strftime("%Y-%m-%d") if hasattr(lu_date, "strftime") else str(lu_date),
            "涨停日期": lu_date.strftime("%Y-%m-%d") if hasattr(lu_date, "strftime") else str(lu_date),
            "MA10触达日": ts,
        }

    # 疑似除权：相对涨停锚点日前 EX_DIV_LOOKBACK 日（不含 L）
    no_ex_div, ex_div_gap, ex_div_date = _prior_ex_div_gap(
        stock_code, stock_name, daily_data, lu_date, EX_DIV_LOOKBACK
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
            "_skip": "涨停日前%d日疑似除权" % int(EX_DIV_LOOKBACK),
            "涨停锚点日": lu_date.strftime("%Y-%m-%d") if hasattr(lu_date, "strftime") else str(lu_date),
            "涨停日期": lu_date.strftime("%Y-%m-%d") if hasattr(lu_date, "strftime") else str(lu_date),
            "疑似除权缺口日": ds,
            "疑似除权开盘缺口%": gap_pct,
        }

    # 以下行情诊断一律相对涨停锚点日 L（本规则 L=选股日）
    close_price = _asof_close(daily_data, lu_date)
    closes = _closes_through(daily_data, lu_date)
    ma5 = _ma(closes, 5)
    ma10 = _ma(closes, 10)
    ma20 = _ma(closes, 20)
    gap = None
    if ma5 is not None and ma10 is not None:
        lo_ma = min(float(ma5), float(ma10))
        if lo_ma > 0:
            gap = abs(float(ma5) - float(ma10)) / lo_ma
    # 涨停次数：L 之前近 RS_LOOKBACK 日（不含 L）
    lu_count, _lu_ago_ex = _recent_lu_stats(
        stock_code, stock_name, daily_data, lu_date, RS_LOOKBACK
    )

    prior_rets = _prior_session_rets(daily_data, lu_date, PRIOR_LOOKBACK)
    max_prior, max_prior_date = _prior_max_ret_with_date(
        daily_data, lu_date, PRIOR_LOOKBACK
    )
    if max_prior is None and prior_rets:
        max_prior = max(prior_rets)
        max_prior_date = None
    thr = _big_move_threshold(stock_code)
    no_big_move = bool(prior_rets) and all(
        not _is_big_move_ret(r, thr) for r in prior_rets
    )

    board_ok, best_rk, best_name, best_kind, hit_str, board_err = _board_top_hit(
        stock_code, sectors, lu_date
    )
    bundle = _day_bundle(lu_date)
    c6 = _code6(stock_code)
    inflow_wan = (bundle.get("inflow_wan") or {}).get(c6)
    inflow_ratio = (bundle.get("inflow_ratio") or {}).get(c6)
    inflow_pct_float = (bundle.get("inflow_pct_of_float") or {}).get(c6)
    inflow_ok = inflow_wan is not None and float(inflow_wan) >= float(MIN_INFLOW_WAN)
    owned_board = _owned_board_extra_fields(stock_code, sectors, bundle)

    # 热门诊断 / 流通市值一律相对涨停锚点日（不用选股日 ctx）
    hot, mv_hot = _hot_fields_for_anchor(stock_code, lu_date)
    mv = (bundle.get("float_mv_yi") or {}).get(c6)
    if mv is None:
        mv = mv_hot
    if mv is not None:
        hot["流通市值_亿"] = round(float(mv), 2)
    try:
        hot["东财榜日期D"] = lu_date.strftime("%Y-%m-%d")
    except Exception:
        hot["东财榜日期D"] = str(lu_date)

    # 选股日 D 对照（价/MA/RS/净流入/板块/市值）；须在 above_ma 之前，供风格包裁剪后仍保留
    contrast = _sel_day_contrast_fields(stock_code, sectors, daily_data, as_of_date)

    above_ma = (
        close_price is not None
        and ma5 is not None
        and ma20 is not None
        and float(close_price) > float(ma5)
        and float(close_price) > float(ma20)
    )

    cond_board = bool(board_ok)
    cond_inflow = bool(inflow_ok)
    cond_prior = bool(no_big_move)
    cond_ma = bool(above_ma)
    # 四项软门槛全真才入选；不判断布林上轨
    meet_all = cond_board and cond_inflow and cond_prior and cond_ma
    fail_reasons = _fail_reasons_logic1(
        cond_board=cond_board,
        cond_inflow=cond_inflow,
        cond_prior=cond_prior,
        cond_ma=cond_ma,
        owned_board=owned_board,
        inflow_wan=inflow_wan,
        max_prior=max_prior,
        thr=thr,
        prior_rets=prior_rets,
        close_price=close_price,
        ma5=ma5,
        ma20=ma20,
        board_err=board_err,
        inflow_err=bundle.get("inflow_err") or "",
    )

    try:
        lu_date_s = lu_date.strftime("%Y-%m-%d")
    except Exception:
        lu_date_s = str(lu_date)

    extra = {
        "热门模式": HOT_MODE,
        "满足条件": bool(meet_all),
        "不满足的原因": fail_reasons,
        "涨停锚点日": lu_date_s,
        "涨停日期": lu_date_s,  # 引擎补全主力净流入/概念排名认此列
        "距涨停交易日数": lu_days_ago if lu_days_ago != "" else 0,
        "前八个交易日最高涨幅": "" if max_prior is None else round(float(max_prior) * 100.0, 4),
        "前八个交易日最高涨幅日期": "" if max_prior_date is None else max_prior_date.strftime("%Y-%m-%d"),
        "条件_当日涨停": bool(is_lu_today),
        "条件_涨停锚点有效": True,
        "条件_涨停后未触MA10": True,
        "条件_行业或概念排名达标": bool(cond_board),
        "条件_行业前N": int(BOARD_TOP_N_INDUSTRY),
        "条件_概念前N": int(BOARD_TOP_N_CONCEPT),
        "条件_主力净流入>=3000万": bool(cond_inflow),
        "条件_前8日无大涨": bool(cond_prior),
        "条件_收盘站上MA5且MA20": bool(cond_ma),
        "除权排查天数": int(EX_DIV_LOOKBACK),
        "除权缺口阈值加EPS": round(float(EX_DIV_EPS) * 100.0, 2),
        "前8日大涨阈值": round(float(thr) * 100.0, 2),
        "所属行业最高排名名次": owned_board["所属行业最高排名名次"],
        "所属行业最高排名名称": owned_board["所属行业最高排名名称"],
        "所属行业最高排名涨幅": owned_board["所属行业最高排名涨幅"],
        "所属行业最高排名净占比": owned_board["所属行业最高排名净占比"],
        "所属概念最高排名名次": owned_board["所属概念最高排名名次"],
        "所属概念最高排名名称": owned_board["所属概念最高排名名称"],
        "所属概念最高排名涨幅": owned_board["所属概念最高排名涨幅"],
        "所属概念最高排名净占比": owned_board["所属概念最高排名净占比"],
        "所属行业排名明细": owned_board.get("所属行业排名明细") or "",
        "所属概念排名明细": owned_board.get("所属概念排名明细") or "",
        "命中前30标签": hit_str,
        "最佳板块排名": "" if best_rk is None else int(best_rk),
        "最佳板块名称": best_name,
        "最佳板块类型": best_kind,
        "主力净流入_万元": "" if inflow_wan is None else round(float(inflow_wan), 2),
        "主力净流入": "" if inflow_wan is None else ("%s万" % round(float(inflow_wan), 2)),
        "主力净流入-净占比": "" if inflow_ratio is None else round(float(inflow_ratio), 4),
        "净流入占流通%": "" if inflow_pct_float is None else round(float(inflow_pct_float), 4),
        "板块排名备注": board_err,
        "主力净流入备注": "" if inflow_wan is not None else (bundle.get("inflow_err") or ""),
        "最近10个交易日内的涨停板数量": int(lu_count),
        "最近的涨停板是几日前": lu_days_ago if lu_days_ago != "" else 0,
        "收盘价": "" if close_price is None else round(float(close_price), 4),
        "MA5": "" if ma5 is None else round(ma5, 4),
        "MA10": "" if ma10 is None else round(ma10, 4),
        "MA20": "" if ma20 is None else round(ma20, 4),
        "均线差占比": "" if gap is None else round(gap, 6),
        "流通市值_亿": "" if mv is None else round(float(mv), 2),
        "TOP_N": int(TOP_N),
        "RS_TOP_K": int(RS_TOP_K),
        "RS_LO": int(RS_LO),
        "RS_HI": RS_HI,
        "RS_TOP_FRAC_NUM": int(RS_TOP_FRAC_NUM),
        "RS_TOP_FRAC_DEN": int(RS_TOP_FRAC_DEN),
        "RS_LOOKBACK": int(RS_LOOKBACK),
        "RS_LOOKBACK_5": int(RS_LOOKBACK_5),
        "RS_LOOKBACK_20": int(RS_LOOKBACK_20),
        "MIN_MEMBERS": int(MIN_MEMBERS),
        "ELIG_LO": int(ELIG_LO),
        "ELIG_HI": int(ELIG_HI),
        "ELIG_HI_SECTOR": int(ELIG_HI_SECTOR),
        "ELIG_HI_CONCEPT": int(ELIG_HI_CONCEPT),
        "MIN_FLOAT_MV_YI": float(MIN_FLOAT_MV_YI),
        "MA_GAP_LO": float(MA_GAP_LO),
        "MA_GAP_HI": float(MA_GAP_HI),
        "ANY_TAG": bool(ANY_TAG),
        "REQUIRE_MIN_FLOAT_MV": bool(REQUIRE_MIN_FLOAT_MV),
        "REQUIRE_MA_GAP": bool(REQUIRE_MA_GAP),
        "REQUIRE_MA_LT_ALIGN": bool(REQUIRE_MA_LT_ALIGN),
        "REQUIRE_NO_RECENT_LU": bool(REQUIRE_NO_RECENT_LU),
        "APPLY_STOCK_FILTERS": bool(APPLY_STOCK_FILTERS),
        "BOARD_TOP_N_INDUSTRY": int(BOARD_TOP_N_INDUSTRY),
        "BOARD_TOP_N_CONCEPT": int(BOARD_TOP_N_CONCEPT),
        "BOARD_TOP_N": int(BOARD_TOP_N),
        "BOARD_SHOW_TOP_N": int(BOARD_SHOW_TOP_N),
        "MIN_INFLOW_WAN": float(MIN_INFLOW_WAN),
        "PRIOR_LOOKBACK": int(PRIOR_LOOKBACK),
        "LU_LOOKBACK": int(LU_LOOKBACK),
        "MA_TOUCH_PERIOD": int(MA_TOUCH_PERIOD),
        "EX_DIV_LOOKBACK": int(EX_DIV_LOOKBACK),
        "EX_DIV_EPS": float(EX_DIV_EPS),
    }
    extra.update(hot)
    extra.update(contrast)
    # 绝对近5/10/20日RS：凡日线够长就写入，不依赖是否进入今日热门成分池
    for _key, _lb in (
        ("近5日RS", RS_LOOKBACK_5),
        ("近10日RS", RS_LOOKBACK),
        ("近20日RS", RS_LOOKBACK_20),
    ):
        rs_abs = _absolute_rs(closes, _lb)
        if rs_abs is not None:
            extra[_key] = round(float(rs_abs), 6)
    if mv is not None and extra.get("流通市值_亿") in ("", None):
        extra["流通市值_亿"] = round(mv, 2)
    # 仅输出满足条件的股票
    if not meet_all:
        return False, {
            "热门模式": HOT_MODE,
            "_skip": "不满足条件",
            "满足条件": False,
            "不满足的原因": fail_reasons,
            "涨停锚点日": lu_date_s,
            "涨停日期": lu_date_s,
        }
    return True, extra
