# 布林%b回落选股
# %b = (现价 - 布林下轨) / (布林上轨 - 布林下轨)；现价=选股日收盘
# 入选硬条件（四项全过）：
#   1) 流通市值 ∈ [MIN_FLOAT_MV_YI, MAX_FLOAT_MV_YI]（默认 80~800 亿）
#   2) 选股日所属行业/概念在涨幅榜上的最好名次 ∈ [BOARD_RANK_LO, BOARD_RANK_HI]（默认 21~100）
#   3) %b 买点 A 或 B（二选一）：
#      A 深度超跌反弹：近 PCTB_LOOKBACK 个交易日内曾 %b<0，且今日 %b>=PCTB_A_MIN（默认 0.05）
#      B 下轨附近企稳：今日 %b<=PCTB_B_MAX（默认 0.04），且 T、T-1 连续两日最低价不再创新低
#   4) 10 日均线不持续向下：近 5 日 MA10 线性回归归一化斜率 Slope_norm > -0.008
#   5) 近 EX_DIV_LOOKBACK_DAYS 个交易日内无疑似除权（不复权 OHLC 跳空过滤）
# 选股日 < 2026-01-01 时优先读 data/daily_full 日线（引擎传入的 daily_cache 不够长时更稳）
# 引擎：关闭热门池收窄，全市场扫描
USE_EM_CANDIDATE_POOL = False
HOT_MODE = "bb_pctb_pullback"

MIN_FLOAT_MV_YI = 80.0
MAX_FLOAT_MV_YI = 800.0
BOARD_RANK_LO = 21
BOARD_RANK_HI = 100
BOARD_RANK_KIND = "chg"  # 东财涨跌幅榜
PCTB_A_MIN = 0.05  # 买点 A：今日 %b 下限
PCTB_B_MAX = 0.04  # 买点 B：今日 %b 上限
PCTB_LOOKBACK = 5  # 买点 A：回溯交易日数（含今日）
BB_PERIOD = 20
BB_K = 2.0
MA10_PERIOD = 10
MA10_SLOPE_DAYS = 5  # 近 N 日 MA10 做线性回归
MA10_SLOPE_NORM_MIN = -0.008  # Slope_norm = k/mean(MA10) 须大于该阈值
EX_DIV_LOOKBACK_DAYS = 20  # 近 N 交易日疑似除权则剔除
DAILY_FULL_BEFORE = (2026, 1, 1)  # 该日之前优先 daily_full

_DAY_CACHE = {}
_STOCK_TAG_CACHE = {"loaded": False, "code_to_tags": {}}
_DAILY_FULL_CACHE = {}  # code6 -> (through_ymd, df)


def _code6(code):
    s = str(code or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits.zfill(6)[-6:] if digits else ""


def _as_date(d):
    if d is None or d == "":
        return None
    if hasattr(d, "year") and hasattr(d, "month") and hasattr(d, "day"):
        try:
            from datetime import date as _date

            if type(d) is _date:
                return d
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


def _ma(closes, n):
    if closes is None or len(closes) < n:
        return None
    try:
        window = [float(x) for x in closes[-n:]]
    except (TypeError, ValueError):
        return None
    if any(x != x or x <= 0 for x in window):
        return None
    return float(sum(window)) / float(n)


def _boll_bands(closes, period=20, k=2.0):
    """返回 (mid, upper, lower)；样本标准差 ddof=1。不足 period 根返回 (None,None,None)。"""
    p = max(2, int(period or 20))
    if closes is None or len(closes) < p:
        return None, None, None
    try:
        window = [float(x) for x in closes[-p:]]
    except (TypeError, ValueError):
        return None, None, None
    if any(x != x or x <= 0 for x in window):
        return None, None, None
    mean = sum(window) / float(p)
    var = sum((x - mean) ** 2 for x in window) / float(p - 1)
    if var != var or var < 0:
        return None, None, None
    std = var ** 0.5
    kk = float(k)
    return mean, mean + kk * std, mean - kk * std


def _pct_b(price, upper, lower):
    if price is None or upper is None or lower is None:
        return None
    try:
        p = float(price)
        u = float(upper)
        lo = float(lower)
    except (TypeError, ValueError):
        return None
    width = u - lo
    if width <= 0 or p != p or u != u or lo != lo:
        return None
    return (p - lo) / width


def _pctb_at_index(closes, idx, period=20, k=2.0):
    """第 idx 根 K 线（0-based）收盘对应的 %b。"""
    if closes is None or idx < 0 or idx >= len(closes):
        return None
    sub = closes[: idx + 1]
    _, upper, lower = _boll_bands(sub, period=period, k=k)
    return _pct_b(sub[-1], upper, lower)


def _recent_pctb_window(closes, lookback=5, period=20, k=2.0):
    """近 lookback 日（含今日）的 %b 序列；不足 BB 窗口则对应位为 None。"""
    if closes is None:
        return []
    n = max(1, int(lookback or 1))
    out = []
    start = max(0, len(closes) - n)
    for i in range(start, len(closes)):
        out.append(_pctb_at_index(closes, i, period=period, k=k))
    return out


def _check_buy_a(closes, pctb_today):
    """A：近 PCTB_LOOKBACK 日曾 %b<0，且今日 %b>=PCTB_A_MIN。"""
    if pctb_today is None or float(pctb_today) < float(PCTB_A_MIN):
        return False, None
    window = _recent_pctb_window(closes, lookback=PCTB_LOOKBACK, period=BB_PERIOD, k=BB_K)
    if not window:
        return False, None
    had_below_zero = any(p is not None and float(p) < 0 for p in window)
    min_pctb = None
    for p in window:
        if p is None:
            continue
        fp = float(p)
        if min_pctb is None or fp < min_pctb:
            min_pctb = fp
    return bool(had_below_zero), min_pctb


def _lows_through(daily_data, as_of_date):
    """取最低价序列（与 closes 对齐）。"""
    if daily_data is None:
        return None
    try:
        if len(daily_data) == 0:
            return None
    except Exception:
        return None
    try:
        import pandas as _pd
        import numpy as _np

        lows = _pd.to_numeric(daily_data["low"], errors="coerce").to_numpy(dtype=float)
        as_d = _as_date(as_of_date)
        if as_d is not None and "date" in getattr(daily_data, "columns", []):
            dser = _pd.to_datetime(daily_data["date"], errors="coerce")
            last = dser.iloc[-1]
            if _pd.notna(last) and last.date() > as_d:
                mask = dser.dt.normalize() <= _pd.Timestamp(as_d)
                lows = lows[_np.asarray(mask)]
        out = [float(x) for x in lows if x == x and x > 0]
        return out if out else None
    except Exception:
        pass
    as_d = _as_date(as_of_date)
    rows = []
    try:
        for _, r in daily_data.iterrows():
            dd = _as_date(r.get("_d") or r.get("date"))
            if dd is None:
                continue
            if as_d is not None and dd > as_d:
                continue
            try:
                lo = float(r.get("low"))
            except (TypeError, ValueError):
                continue
            if lo == lo and lo > 0:
                rows.append(lo)
    except Exception:
        return None
    return rows if rows else None


def _lows_no_new_low_2d(lows):
    """T、T-1 连续两日最低价不再创新低。

    T-1 不低于 T-2 之前最低；T 不低于 T-1（企稳，不再下探）。
    """
    if lows is None or len(lows) < 3:
        return None
    lo_t = float(lows[-1])
    lo_t1 = float(lows[-2])
    prior = [float(x) for x in lows[:-2] if x == x and x > 0]
    if not prior:
        return None
    ref = min(prior)
    return bool(lo_t1 >= ref and lo_t >= lo_t1)


def _check_buy_b(closes, lows, pctb_today):
    """B：今日 %b<=PCTB_B_MAX，且 T/T-1 不再创新低。"""
    if pctb_today is None or float(pctb_today) > float(PCTB_B_MAX):
        return False, None
    stable = _lows_no_new_low_2d(lows)
    if stable is None:
        return False, None
    return bool(stable), stable


def _check_pctb_entry(closes, lows, pctb_today):
    """返回 (ok, buy_kind, detail_dict)。buy_kind: A|B|''。"""
    ok_a, min_pctb = _check_buy_a(closes, pctb_today)
    if ok_a:
        return True, "A", {"近窗最低%b": min_pctb}
    ok_b, stable = _check_buy_b(closes, lows, pctb_today)
    if ok_b:
        return True, "B", {"T_T1不再创新低": stable}
    return False, "", {}


def _closes_through(daily_data, as_of_date):
    """取收盘序列。引擎 / daily_full(through_date) 通常已截到 as_of，直接读 close。"""
    if daily_data is None:
        return None
    try:
        if len(daily_data) == 0:
            return None
    except Exception:
        return None
    try:
        import pandas as _pd
        import numpy as _np

        closes = _pd.to_numeric(daily_data["close"], errors="coerce").to_numpy(dtype=float)
        # 引擎偶发未截断时再按日过滤
        as_d = _as_date(as_of_date)
        if as_d is not None and "date" in getattr(daily_data, "columns", []):
            dser = _pd.to_datetime(daily_data["date"], errors="coerce")
            last = dser.iloc[-1]
            if _pd.notna(last) and last.date() > as_d:
                mask = dser.dt.normalize() <= _pd.Timestamp(as_d)
                closes = closes[_np.asarray(mask)]
        out = [float(x) for x in closes if x == x and x > 0]
        return out if out else None
    except Exception:
        pass
    as_d = _as_date(as_of_date)
    rows = []
    try:
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
    except Exception:
        return None
    return rows if rows else None


def _need_daily_full(as_of_date):
    d = _as_date(as_of_date)
    if d is None:
        return False
    try:
        from datetime import date as _date

        cutoff = _date(*DAILY_FULL_BEFORE)
        return d < cutoff
    except Exception:
        return d.year < 2026


def _resolve_daily(stock_code, daily_data, as_of_date):
    """选股日早于 2026 时优先 daily_full；否则用引擎传入日线。"""
    as_d = _as_date(as_of_date)
    if _need_daily_full(as_d):
        c6 = _code6(stock_code)
        ymd = _ymd(as_d)
        cached = _DAILY_FULL_CACHE.get(c6)
        if cached is not None and cached[0] >= ymd and cached[1] is not None:
            try:
                if len(cached[1]) > 0:
                    return cached[1], "daily_full"
            except Exception:
                pass
        try:
            from utils.data_sync_request import load_full_daily

            df = load_full_daily(stock_code, through_date=as_d, adjust="none")
            if df is not None:
                try:
                    if len(df) > 0:
                        _DAILY_FULL_CACHE[c6] = (ymd, df)
                        return df, "daily_full"
                except Exception:
                    pass
        except Exception:
            pass
    return daily_data, "engine"


def _ma10_slope_ok(closes, period=10, slope_days=5, slope_norm_min=-0.008):
    """方法 A：近 slope_days 日 MA10 对时间 0..n-1 线性回归。

    Slope_norm = k / mean(MA10_recent)；通过条件：Slope_norm > slope_norm_min。
    返回 (ok|None, mas, k, slope_norm)。
    """
    p = max(1, int(period or 10))
    n = max(2, int(slope_days or 5))
    need = p + n - 1
    if closes is None or len(closes) < need:
        return None, [], None, None
    mas = []
    for i in range(n):
        end = len(closes) - (n - 1 - i)
        m = _ma(closes[:end], p)
        if m is None:
            return None, mas, None, None
        mas.append(float(m))
    # 线性回归：x=0..n-1，y=mas；k = Cov(x,y)/Var(x)
    xs = list(range(n))
    mean_x = sum(xs) / float(n)
    mean_y = sum(mas) / float(n)
    if mean_y == 0 or mean_y != mean_y:
        return None, mas, None, None
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x <= 0:
        return None, mas, None, None
    cov_xy = sum((xs[i] - mean_x) * (mas[i] - mean_y) for i in range(n))
    k = cov_xy / var_x
    slope_norm = k / mean_y
    ok = bool(slope_norm > float(slope_norm_min))
    return ok, mas, float(k), float(slope_norm)


def _load_stock_tags():
    if _STOCK_TAG_CACHE.get("loaded"):
        return _STOCK_TAG_CACHE.get("code_to_tags") or {}
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


def _tags_for(stock_code, sectors):
    c6 = _code6(stock_code)
    tags = set()
    m = _load_stock_tags()
    if c6 and isinstance(m, dict):
        got = m.get(c6)
        if isinstance(got, (list, tuple, set)):
            for t in got:
                s = str(t or "").strip()
                if s:
                    tags.add(s)
        elif isinstance(got, str) and got.strip():
            tags.add(got.strip())
    if sectors:
        if isinstance(sectors, str):
            for part in sectors.replace("，", ",").replace(";", ",").split(","):
                s = part.strip()
                if s:
                    tags.add(s)
        elif isinstance(sectors, (list, tuple, set)):
            for t in sectors:
                s = str(t or "").strip()
                if s:
                    tags.add(s)
    return tags


def _day_bundle(as_of_date):
    """按日缓存涨幅榜名次 + 流通市值快照。

    不做全市场「代码→所属板块」展开（成员表扫描极慢）；
    个股名次用 stock_info 标签 / sectors ∩ 当日榜匹配即可。
    """
    key = _ymd(as_of_date) or str(as_of_date)
    if key in _DAY_CACHE:
        return _DAY_CACHE[key]
    bundle = {
        "ind_rank": {},
        "con_rank": {},
        "ind_chg": {},
        "con_chg": {},
        "float_mv_yi": {},
        "code_best_industry": {},
        "code_best_concept": {},
        "board_err": "",
        "mv_err": "",
    }
    try:
        from utils.eastmoney_board_rank_ctx import (
            board_rank_csv_paths,
            _load_rank_chg_maps,
        )

        paths = board_rank_csv_paths(as_of_date)
        ind_rank, ind_chg = _load_rank_chg_maps(paths.get("industry") or "")
        con_rank, con_chg = _load_rank_chg_maps(paths.get("concept") or "")
        bundle["ind_rank"] = ind_rank
        bundle["con_rank"] = con_rank
        bundle["ind_chg"] = ind_chg
        bundle["con_chg"] = con_chg
        if not ind_rank and not con_rank:
            bundle["board_err"] = "无东财行业/概念涨幅榜"
    except Exception as e:
        bundle["board_err"] = "加载板块涨幅榜失败:%s" % e

    try:
        from utils.eastmoney_board_rank_ctx import _load_float_mv_yi_for_day

        mv_map = _load_float_mv_yi_for_day(_as_date(as_of_date))
        if isinstance(mv_map, dict):
            bundle["float_mv_yi"] = mv_map
        if not bundle["float_mv_yi"]:
            bundle["mv_err"] = "近邻无快照，将按最近快照股本×收盘估算"
    except Exception as e:
        bundle["mv_err"] = "加载流通市值失败:%s" % e

    _DAY_CACHE[key] = bundle
    return bundle


def _resolve_mv_yi(stock_code, as_of_date, close_px, bundle):
    """优先近邻快照；否则用最近快照股本 × 选股日收盘估算。"""
    c6 = _code6(stock_code)
    near = (bundle or {}).get("float_mv_yi") or {}
    if c6 and c6 in near:
        try:
            return float(near[c6]), "snapshot_near"
        except (TypeError, ValueError):
            pass
    try:
        from utils.eastmoney_board_rank_ctx import resolve_float_mv_yi

        return resolve_float_mv_yi(as_of_date, stock_code, close_px)
    except Exception as e:
        return None, "估算失败:%s" % e


def _best_board_rank(stock_code, sectors, bundle):
    """返回 (best_rank, best_name, best_kind, ind_rk, con_rk)。最好=名次数字最小。"""
    c6 = _code6(stock_code)
    ind_info = (bundle.get("code_best_industry") or {}).get(c6) if c6 else None
    con_info = (bundle.get("code_best_concept") or {}).get(c6) if c6 else None

    def _from_info(info):
        if not isinstance(info, dict):
            return None, ""
        try:
            rk = info.get("rank")
            if rk is None or rk == "":
                return None, ""
            return int(rk), str(info.get("name") or "")
        except (TypeError, ValueError):
            return None, ""

    ind_rk, ind_name = _from_info(ind_info)
    con_rk, con_name = _from_info(con_info)

    # 反查缺失时，用 sectors/标签 ∩ 当日榜兜底
    if ind_rk is None or con_rk is None:
        tags = _tags_for(stock_code, sectors)
        if tags:
            if ind_rk is None:
                for t in tags:
                    rk = (bundle.get("ind_rank") or {}).get(t)
                    if rk is None:
                        continue
                    try:
                        rki = int(rk)
                    except (TypeError, ValueError):
                        continue
                    if ind_rk is None or rki < ind_rk:
                        ind_rk, ind_name = rki, t
            if con_rk is None:
                for t in tags:
                    rk = (bundle.get("con_rank") or {}).get(t)
                    if rk is None:
                        continue
                    try:
                        rki = int(rk)
                    except (TypeError, ValueError):
                        continue
                    if con_rk is None or rki < con_rk:
                        con_rk, con_name = rki, t

    best_rk = None
    best_name = ""
    best_kind = ""
    cands = []
    if ind_rk is not None:
        cands.append((ind_rk, ind_name, "行业"))
    if con_rk is not None:
        cands.append((con_rk, con_name, "概念"))
    if cands:
        best_rk, best_name, best_kind = min(cands, key=lambda x: x[0])
    return best_rk, best_name, best_kind, ind_rk, con_rk, ind_name, con_name


def select(stock_code, stock_name, sectors, daily_data, as_of_date, ctx):
    # 注意：不要读取东财热门 ctx 键，否则引擎会按日全量预载（极慢）。
    _ = ctx  # 保留签名；本规则不依赖引擎 ctx

    daily, daily_src = _resolve_daily(stock_code, daily_data, as_of_date)
    closes = _closes_through(daily, as_of_date)
    if not closes:
        return False, {
            "热门模式": HOT_MODE,
            "_skip": "无日线收盘",
            "日线来源": daily_src,
        }

    try:
        from utils.ex_div_filter import has_suspected_ex_div_in_last_n_trading_days

        if has_suspected_ex_div_in_last_n_trading_days(
            daily,
            as_of_date,
            EX_DIV_LOOKBACK_DAYS,
            code6=_code6(stock_code),
        ):
            return False, {
                "热门模式": HOT_MODE,
                "_skip": "近%d日疑似除权" % int(EX_DIV_LOOKBACK_DAYS),
                "日线来源": daily_src,
                "EX_DIV_LOOKBACK_DAYS": int(EX_DIV_LOOKBACK_DAYS),
            }
    except Exception as e:
        return False, {
            "热门模式": HOT_MODE,
            "_skip": "除权过滤异常",
            "除权过滤错误": str(e),
            "日线来源": daily_src,
        }

    close_px = float(closes[-1])
    mid, upper, lower = _boll_bands(closes, period=BB_PERIOD, k=BB_K)
    pctb = _pct_b(close_px, upper, lower)

    lows = _lows_through(daily, as_of_date)
    cond_pctb, buy_kind, pctb_detail = _check_pctb_entry(closes, lows, pctb)
    # 绝大多数票 %b 买点不过：先拒，不碰板块/市值 I/O
    if not cond_pctb:
        return False, {
            "热门模式": HOT_MODE,
            "_skip": "%b买点不满足",
            "%b": "" if pctb is None else round(float(pctb), 6),
            "日线来源": daily_src,
        }

    ma10 = _ma(closes, MA10_PERIOD)
    ma10_ok, ma10_series, ma10_k, ma10_slope_norm = _ma10_slope_ok(
        closes,
        period=MA10_PERIOD,
        slope_days=MA10_SLOPE_DAYS,
        slope_norm_min=MA10_SLOPE_NORM_MIN,
    )
    cond_ma10 = bool(ma10_ok)
    if not cond_ma10:
        return False, {
            "热门模式": HOT_MODE,
            "_skip": "MA10斜率不满足",
            "MA10归一化斜率": (
                "" if ma10_slope_norm is None else round(float(ma10_slope_norm), 8)
            ),
            "日线来源": daily_src,
        }

    bundle = _day_bundle(as_of_date)
    mv, mv_src = _resolve_mv_yi(stock_code, as_of_date, close_px, bundle)
    best_rk, best_name, best_kind, ind_rk, con_rk, ind_name, con_name = _best_board_rank(
        stock_code, sectors, bundle
    )

    cond_mv = (
        mv is not None
        and float(MIN_FLOAT_MV_YI) <= float(mv) <= float(MAX_FLOAT_MV_YI)
    )
    cond_board = (
        best_rk is not None
        and int(BOARD_RANK_LO) <= int(best_rk) <= int(BOARD_RANK_HI)
    )

    if not cond_mv:
        return False, {
            "热门模式": HOT_MODE,
            "_skip": "流通市值不满足",
            "流通市值_亿": "" if mv is None else round(float(mv), 2),
            "流通市值来源": mv_src or "",
            "日线来源": daily_src,
        }
    if not cond_board:
        return False, {
            "热门模式": HOT_MODE,
            "_skip": "板块最好排名不满足",
            "最佳板块排名": "" if best_rk is None else int(best_rk),
            "最佳板块名称": best_name or "",
            "日线来源": daily_src,
        }

    ok = True
    extra = {
        "热门模式": HOT_MODE,
        "满足条件": True,
        "不满足的原因": "",
        "买点": buy_kind,
        "条件_流通市值80to800亿": True,
        "条件_板块最好排名21to100": True,
        "条件_%b买点A或B": True,
        "条件_MA10不持续向下": True,
        "MA10斜率k": "" if ma10_k is None else round(float(ma10_k), 8),
        "MA10归一化斜率": (
            "" if ma10_slope_norm is None else round(float(ma10_slope_norm), 8)
        ),
        "MA10_SLOPE_NORM_MIN": float(MA10_SLOPE_NORM_MIN),
        "流通市值_亿": "" if mv is None else round(float(mv), 2),
        "流通市值来源": mv_src or "",
        "MIN_FLOAT_MV_YI": float(MIN_FLOAT_MV_YI),
        "MAX_FLOAT_MV_YI": float(MAX_FLOAT_MV_YI),
        "所属行业最高排名名次": "" if ind_rk is None else int(ind_rk),
        "所属行业最高排名名称": ind_name or "",
        "所属概念最高排名名次": "" if con_rk is None else int(con_rk),
        "所属概念最高排名名称": con_name or "",
        "最佳板块排名": "" if best_rk is None else int(best_rk),
        "最佳板块名称": best_name or "",
        "最佳板块类型": best_kind or "",
        "BOARD_RANK_LO": int(BOARD_RANK_LO),
        "BOARD_RANK_HI": int(BOARD_RANK_HI),
        "BOARD_RANK_KIND": str(BOARD_RANK_KIND),
        "收盘价": round(close_px, 4),
        "布林中轨": "" if mid is None else round(float(mid), 4),
        "布林上轨": "" if upper is None else round(float(upper), 4),
        "布林下轨": "" if lower is None else round(float(lower), 4),
        "%b": "" if pctb is None else round(float(pctb), 6),
        "PCTB_A_MIN": float(PCTB_A_MIN),
        "PCTB_B_MAX": float(PCTB_B_MAX),
        "PCTB_LOOKBACK": int(PCTB_LOOKBACK),
        "BB_PERIOD": int(BB_PERIOD),
        "BB_K": float(BB_K),
        "MA10": "" if ma10 is None else round(float(ma10), 4),
        "MA10近窗": ",".join("%.4f" % float(x) for x in ma10_series) if ma10_series else "",
        "MA10_SLOPE_DAYS": int(MA10_SLOPE_DAYS),
        "日线来源": daily_src,
        "日线根数": len(closes),
        "板块排名备注": bundle.get("board_err") or "",
        "流通市值备注": "" if mv is not None else (mv_src or bundle.get("mv_err") or ""),
    }
    if buy_kind == "A" and pctb_detail.get("近窗最低%b") is not None:
        extra["近窗最低%b"] = round(float(pctb_detail["近窗最低%b"]), 6)
    if buy_kind == "B":
        extra["T_T1不再创新低"] = bool(pctb_detail.get("T_T1不再创新低"))
        if lows and len(lows) >= 2:
            extra["T日最低"] = round(float(lows[-1]), 4)
            extra["T1日最低"] = round(float(lows[-2]), 4)
    return bool(ok), extra
