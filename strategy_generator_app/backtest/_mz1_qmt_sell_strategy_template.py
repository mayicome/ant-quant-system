#coding:utf-8
"""马总逻辑1卖 细回测 · QMT 模型交易动态策略（由蚂蚁量化系统生成）

推荐：主程序「探针」热加载本文件（写到 userdata/mz1_qmt_fine_watch/current.py）。
结果：print 到 userdata/log/XtClient_FormulaOutput_YYYYMMDD.log，前缀 ANT_MZ1_SELL_BT|
勿写 D:\\蚂蚁量化系统（Forbidden FileIO）。
Python 需 3.6 兼容；tick/日线只用大 QMT 内置 download_history_data + ContextInfo.get_market_data_ex(subscribe=False)。
无 tick 日 SKIP，禁止日线 OHLC 兜底撮合。
"""
from __future__ import print_function

import json
import time
import traceback
from datetime import date, datetime, timedelta

# ===== embedded config =====
CFG = json.loads(r"""___CFG_JSON___""")
PREFIX = CFG.get("result_prefix") or "ANT_MZ1_SELL_BT|"
# ===========================


def _ensure_unicodedata():
    try:
        import unicodedata  # noqa: F401
        return "ok"
    except Exception:
        pass
    import sys
    import types
    m = types.ModuleType("unicodedata")

    def normalize(form, unistr):
        return unistr

    def category(c):
        return "Lo"

    m.normalize = normalize
    m.category = category
    m.unidata_version = "9.0.0"
    m.ucd_3_2_0 = m
    sys.modules["unicodedata"] = m
    return "stub"


_UNICODEDATA_STATUS = _ensure_unicodedata()


def _log(msg):
    try:
        print("%sLOG|%s" % (PREFIX, msg))
    except Exception:
        pass


def _emit(kind, payload):
    try:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        body = str(payload)
    try:
        print("%s%s|%s" % (PREFIX, kind, body))
    except Exception:
        pass


def _parse_d(s):
    try:
        s = str(s or "").strip()
        if not s:
            return None
        if len(s) >= 10 and s[4] == "-" and s[7] == "-":
            return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
        if len(s) >= 8 and s.isdigit():
            return date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
    except Exception:
        return None
    return None


def _code6(full_code):
    s = str(full_code or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def _full_from_c6(c6):
    c6 = _code6(c6)
    if not c6:
        return ""
    if c6.startswith("6"):
        return c6 + ".SH"
    return c6 + ".SZ"


def _resolve_fn(name):
    if name in globals() and callable(globals().get(name)):
        return globals().get(name)
    try:
        import builtins
    except ImportError:
        builtins = None
    if builtins is not None:
        fn = getattr(builtins, name, None)
        if callable(fn):
            return fn
    try:
        import __main__ as main_mod
        fn = getattr(main_mod, name, None)
        if callable(fn):
            return fn
    except Exception:
        pass
    return None


def _call_download(ContextInfo, full_code, period, start_s, end_s):
    fn = None
    if ContextInfo is not None:
        fn = getattr(ContextInfo, "download_history_data", None)
    if not callable(fn):
        fn = _resolve_fn("download_history_data")
    if not callable(fn):
        return "no_download_history_data"
    try:
        fn(full_code, period, start_s, end_s)
        return ""
    except TypeError:
        try:
            fn(full_code, period, start_s, end_s, None)
            return ""
        except Exception as e:
            return str(e)[:200]
    except Exception as e:
        return str(e)[:200]


def _extract_payload(data, full_code):
    if data is None:
        return None
    if isinstance(data, dict):
        if full_code in data:
            return data.get(full_code)
        c6 = _code6(full_code)
        if c6 in data:
            return data.get(c6)
        if len(data) == 1:
            return list(data.values())[0]
    return data


def _nrows(payload):
    if payload is None:
        return 0
    if isinstance(payload, dict):
        for k in ("time", "lastPrice", "last", "close", "Open"):
            v = payload.get(k)
            if isinstance(v, (list, tuple)):
                return len(v)
        try:
            return len(next(iter(payload.values())))
        except Exception:
            return 0
    try:
        return len(payload)
    except Exception:
        return 0


def _get_market(ContextInfo, full_code, period, start_s, end_s):
    if ContextInfo is None:
        return None, "no_ContextInfo"
    fn = getattr(ContextInfo, "get_market_data_ex", None)
    if not callable(fn):
        return None, "no_ctx_get_market_data_ex"
    fields = []
    stocks = [full_code]
    last_err = ""
    try:
        data = fn(
            fields, stocks, period=period, start_time=start_s, end_time=end_s,
            count=-1, dividend_type="none", fill_data=False, subscribe=False,
        )
        return _extract_payload(data, full_code), ""
    except Exception as e:
        last_err = str(e)[:200]
    try:
        data = fn(fields, stocks, period, start_s, end_s, -1, "none", False, False)
        return _extract_payload(data, full_code), ""
    except Exception as e:
        last_err = str(e)[:200]
    try:
        data = fn(fields, full_code, period, start_s, end_s, -1, "none", False, False)
        return _extract_payload(data, full_code), ""
    except Exception as e:
        last_err = str(e)[:200]
    fn2 = getattr(ContextInfo, "get_market_data", None)
    if callable(fn2):
        try:
            data = fn2(
                fields, stock_code=stocks, start_time=start_s, end_time=end_s,
                skip_paused=True, period=period, dividend_type="none", count=-1,
            )
            return _extract_payload(data, full_code), ""
        except Exception as e:
            last_err = str(e)[:200]
    return None, last_err or "get_market_failed"


def _series(payload, *names):
    if payload is None:
        return []
    cols = getattr(payload, "columns", None)
    if cols is not None and hasattr(payload, "__getitem__"):
        for n in names:
            try:
                if n in cols:
                    return list(payload[n].values)
            except Exception:
                pass
        if "time" in names or "stamp" in names:
            try:
                return list(payload.index)
            except Exception:
                pass
    if isinstance(payload, dict):
        for n in names:
            v = payload.get(n)
            if isinstance(v, (list, tuple)):
                return list(v)
    return []


def _as_ymd_list(ts_list):
    out = []
    for tm in ts_list or []:
        ymd = _tick_ymd(tm)
        out.append(ymd or "")
    return out


def _tick_ymd(tm):
    if tm is None:
        return None
    if isinstance(tm, datetime):
        return tm.strftime("%Y%m%d")
    if isinstance(tm, date):
        return tm.strftime("%Y%m%d")
    s = str(tm).strip()
    if not s:
        return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[0:4] + s[5:7] + s[8:10]
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 8:
        return digits[:8]
    try:
        v = float(s)
        if v >= 1e12:
            return datetime.fromtimestamp(v / 1e3).strftime("%Y%m%d")
        if v >= 1e9:
            return datetime.fromtimestamp(v).strftime("%Y%m%d")
    except Exception:
        pass
    return None


def _tick_tod_sec(tm):
    if tm is None:
        return None
    if isinstance(tm, datetime):
        return tm.hour * 3600 + tm.minute * 60 + tm.second
    if hasattr(tm, "hour"):
        try:
            return int(tm.hour) * 3600 + int(tm.minute) * 60 + int(getattr(tm, "second", 0) or 0)
        except Exception:
            pass
    s = str(tm).strip()
    if " " in s:
        s = s.split(" ", 1)[-1]
    if len(s) >= 8 and s[2] == ":" and s[5] == ":":
        try:
            return int(s[0:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:8])
        except Exception:
            return None
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 14:
        try:
            return int(digits[-6:-4]) * 3600 + int(digits[-4:-2]) * 60 + int(digits[-2:])
        except Exception:
            return None
    try:
        v = float(str(tm).strip())
        if v >= 1e12:
            dt = datetime.fromtimestamp(v / 1e3)
            return dt.hour * 3600 + dt.minute * 60 + dt.second
        if v >= 1e9:
            dt = datetime.fromtimestamp(v)
            return dt.hour * 3600 + dt.minute * 60 + dt.second
    except Exception:
        pass
    return None


def _fmt_tick_time(tm):
    sec = _tick_tod_sec(tm)
    if sec is None:
        return ""
    h = sec // 3600
    m = (sec % 3600) // 60
    s = sec % 60
    return "%02d:%02d:%02d" % (h, m, s)


def _parse_hms(s):
    s = str(s or "").strip()
    parts = s.split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        sec = int(float(parts[2])) if len(parts) > 2 else 0
        return h * 3600 + m * 60 + sec
    except Exception:
        return None


def _is_growth_board(c6):
    c6 = _code6(c6)
    if not c6:
        return False
    return c6.startswith("3") or c6.startswith("688") or c6.startswith("8") or c6.startswith("4")


def _limit_ratio(c6):
    return 0.20 if _is_growth_board(c6) else 0.10


def _half_lot(total):
    total = int(total)
    if total < 100:
        return 0
    total = (total // 100) * 100
    return max(100, (int(total * 0.5 / 100) * 100))


def _full_lot(avail):
    avail = int(avail)
    if avail < 100:
        return 0
    return (avail // 100) * 100


def _half_pair_absorb_dust(total):
    total = _full_lot(total)
    if total < 100:
        return 0, 0
    a = _half_lot(total)
    b = total - a
    if b < 100:
        return total, 0
    return a, b


def _fold_dust(sell_vol, hold, price_min, min_amt):
    sell_vol = _full_lot(sell_vol)
    hold = _full_lot(hold)
    if sell_vol <= 0 or hold <= 0:
        return 0
    sell_vol = min(sell_vol, hold)
    remain = hold - sell_vol
    if remain <= 0:
        return sell_vol
    if remain < 100:
        return hold
    if min_amt > 0 and price_min > 0 and remain * float(price_min) < min_amt:
        return hold
    return sell_vol


def _clamp_px(px, ld, lu):
    px = float(px)
    if ld > 0 and lu > 0:
        return max(ld, min(lu, px))
    return px


def _weekday_days(start_d, end_d):
    out = []
    d = start_d
    while d <= end_d:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _recent_lu_trigger_from_daily(c6, closes_by_ymd, ymds_ord, as_d):
    """近10日涨停价（不复权 close 近似）：返回最近一次涨停日的涨停价。"""
    if not ymds_ord:
        return None
    as_s = as_d.strftime("%Y%m%d")
    prevs = [y for y in ymds_ord if y < as_s]
    if len(prevs) < 2:
        return None
    window = prevs[-11:]
    lr = _limit_ratio(c6)
    for i in range(len(window) - 1, 0, -1):
        y0 = window[i - 1]
        y1 = window[i]
        prev_c = float(closes_by_ymd.get(y0) or 0)
        cur_c = float(closes_by_ymd.get(y1) or 0)
        if prev_c <= 0 or cur_c <= 0:
            continue
        lim = round(prev_c * (1.0 + lr), 2)
        inc = (cur_c - prev_c) / prev_c
        if abs(cur_c - lim) < 0.02 or inc >= lr * 0.99:
            return float(lim)
    return None


def _daily_maps(ContextInfo, full_code, start_s, end_s):
    payload, err = _get_market(ContextInfo, full_code, "1d", start_s, end_s)
    if err or payload is None:
        return {}, [], err or "no_daily"
    times = _series(payload, "time", "stamp")
    closes = _series(payload, "close", "Close", "lastPrice", "last")
    opens = _series(payload, "open", "Open")
    highs = _series(payload, "high", "High")
    lows = _series(payload, "low", "Low")
    ymds = _as_ymd_list(times)
    close_by = {}
    open_by = {}
    high_by = {}
    low_by = {}
    ord_ymds = []
    n = min(len(ymds), len(closes) if closes else 0)
    if not closes:
        n = len(ymds)
    for i in range(len(ymds)):
        y = ymds[i]
        if not y:
            continue
        ord_ymds.append(y)
        try:
            if closes and i < len(closes):
                close_by[y] = float(closes[i] or 0)
        except Exception:
            pass
        try:
            if opens and i < len(opens):
                open_by[y] = float(opens[i] or 0)
        except Exception:
            pass
        try:
            if highs and i < len(highs):
                high_by[y] = float(highs[i] or 0)
        except Exception:
            pass
        try:
            if lows and i < len(lows):
                low_by[y] = float(lows[i] or 0)
        except Exception:
            pass
    return {
        "close": close_by,
        "open": open_by,
        "high": high_by,
        "low": low_by,
    }, ord_ymds, ""


def _ticks_for_day(ContextInfo, full_code, day_d, run_start_sec, run_end_sec):
    day_s = day_d.strftime("%Y%m%d")
    payload, err = _get_market(ContextInfo, full_code, "tick", day_s, day_s)
    if err or payload is None or _nrows(payload) <= 0:
        return [], err or "no_tick"
    times = _series(payload, "time", "stamp")
    lasts = _series(payload, "lastPrice", "last", "close", "Close")
    highs = _series(payload, "high", "High")
    lows = _series(payload, "low", "Low")
    if not times or not lasts:
        return [], "empty_tick_fields"
    out = []
    n = min(len(times), len(lasts))
    for i in range(n):
        sec = _tick_tod_sec(times[i])
        if sec is None:
            continue
        if run_start_sec is not None and sec < run_start_sec:
            continue
        if run_end_sec is not None and sec > run_end_sec:
            continue
        try:
            last = float(lasts[i] or 0)
        except Exception:
            last = 0.0
        if last <= 0:
            continue
        hi = last
        lo = last
        try:
            if highs and i < len(highs) and highs[i]:
                hi = float(highs[i])
        except Exception:
            pass
        try:
            if lows and i < len(lows) and lows[i]:
                lo = float(lows[i])
        except Exception:
            pass
        out.append((sec, times[i], last, hi, lo))
    return out, ""


def _apply_buy_fill(state, c6, vol, price, buy_d, blotter_only):
    st = state.setdefault(c6, {
        "volume": 0, "cost": 0.0, "baseline": 0,
        "entry_date": None, "last_buy_date": None,
    })
    vol = int(vol)
    if vol <= 0:
        return
    if blotter_only:
        # already in initial_positions; only refresh last_buy if older missing
        if st.get("last_buy_date") is None:
            st["last_buy_date"] = buy_d
        return
    old_v = int(st.get("volume") or 0)
    old_c = float(st.get("cost") or 0)
    new_v = old_v + vol
    if new_v > 0:
        st["cost"] = (old_c * old_v + float(price) * vol) / float(new_v)
    st["volume"] = new_v
    st["baseline"] = int(st.get("baseline") or 0) + vol
    if st.get("entry_date") is None:
        st["entry_date"] = buy_d
    st["last_buy_date"] = buy_d


def _available_qty(st, day_d, from_t1):
    hold = _full_lot(int(st.get("volume") or 0))
    if hold < 100:
        return 0
    if not from_t1:
        return hold
    lb = st.get("last_buy_date")
    if lb is None:
        return hold
    if isinstance(lb, str):
        lb = _parse_d(lb)
    if lb is None:
        return hold
    # T+1: last buy day strictly before today
    if lb < day_d:
        return hold
    return 0


def _sell_day_index(st, day_d, trading_days):
    """引擎序号（含买入日）：买入日=1。"""
    anchor = st.get("last_buy_date") or st.get("entry_date")
    if isinstance(anchor, str):
        anchor = _parse_d(anchor)
    if anchor is None:
        return 0
    idx = 0
    for d in trading_days:
        if d < anchor:
            continue
        if d > day_d:
            break
        idx += 1
        if d == day_d:
            return idx
    return idx


def _replay_best_sell(ticks, trigger, drop_pct, run_start_sec, run_end_sec):
    """先上破 trigger，再按峰值回落 drop% 成交；drop=0 则触价即成。返回 (sec, tm, px) 或 None。"""
    if trigger <= 0 or not ticks:
        return None
    armed = False
    peak = 0.0
    floor_mult = 1.0 - float(drop_pct) / 100.0
    for sec, tm, last, hi, lo in ticks:
        if run_start_sec is not None and sec < run_start_sec:
            continue
        if run_end_sec is not None and sec > run_end_sec:
            continue
        use_hi = hi if hi > 0 else last
        use_lo = lo if lo > 0 else last
        if not armed:
            if use_hi + 1e-9 >= trigger or last + 1e-9 >= trigger:
                armed = True
                peak = max(use_hi, last, trigger)
                if float(drop_pct) <= 1e-12:
                    return (sec, tm, last if last > 0 else trigger)
            continue
        peak = max(peak, use_hi, last)
        pull = peak * floor_mult
        if use_lo <= pull + 1e-9 or last <= pull + 1e-9:
            px = last if last > 0 else pull
            return (sec, tm, px)
    return None


def _replay_single_sell(ticks, price, run_start_sec, run_end_sec):
    if price <= 0 or not ticks:
        return None
    for sec, tm, last, hi, lo in ticks:
        if run_start_sec is not None and sec < run_start_sec:
            continue
        if run_end_sec is not None and sec > run_end_sec:
            continue
        use_hi = hi if hi > 0 else last
        if use_hi + 1e-9 >= price or last + 1e-9 >= price:
            return (sec, tm, last if last > 0 else price)
    return None


def _replay_scheduled_clear(ticks, clear_px, clear_sec, force, run_end_sec):
    if not ticks:
        return None
    for sec, tm, last, hi, lo in ticks:
        if sec < clear_sec:
            continue
        if run_end_sec is not None and sec > run_end_sec:
            break
        if force:
            return (sec, tm, last)
        if clear_px > 0 and last + 1e-9 < clear_px:
            return (sec, tm, last)
    return None


def _run_backtest(ContextInfo):
    t0 = time.time()
    start_d = _parse_d(CFG.get("start"))
    end_d = _parse_d(CFG.get("end"))
    if start_d is None or end_d is None or end_d < start_d:
        _emit("ERROR", {"msg": "bad_start_end", "start": CFG.get("start"), "end": CFG.get("end")})
        return
    run_tag = str(CFG.get("run_tag") or "")
    params = CFG.get("params") or {}
    if not isinstance(params, dict):
        params = {}
    from_t1 = bool(CFG.get("from_t1", True))
    try:
        ui_n = int(CFG.get("sell_hold_from_next_day") or params.get("sell_hold_from_next_day") or 2)
    except Exception:
        ui_n = 2
    hold_n = None
    for k in ("scheduled_clear_on_sell_day", "sell_hold_trading_days"):
        try:
            v = int(params.get(k) or 0)
        except Exception:
            v = 0
        if v >= 1:
            hold_n = v
            break
    if hold_n is None:
        hold_n = ui_n + 1
    clear_time = str(CFG.get("scheduled_clear_time") or params.get("scheduled_clear_time") or "14:56:00")
    clear_sec = _parse_hms(clear_time) or (14 * 3600 + 56 * 60)
    run_start_sec = _parse_hms(CFG.get("strategy_run_start_time") or "09:30") or (9 * 3600 + 30 * 60)
    run_end_sec = _parse_hms(CFG.get("strategy_run_end_time") or "15:00") or (15 * 3600)

    try:
        open_gain_main = float(params.get("open_gain_main") if params.get("open_gain_main") is not None else 0.05)
    except Exception:
        open_gain_main = 0.05
    try:
        open_gain_growth = float(params.get("open_gain_growth") if params.get("open_gain_growth") is not None else 0.08)
    except Exception:
        open_gain_growth = 0.08
    try:
        open_drop = float(params.get("drop_percent") if params.get("drop_percent") is not None else 1.5)
    except Exception:
        open_drop = 1.5
    try:
        lu_drop = float(params.get("lu_drop_percent") if params.get("lu_drop_percent") is not None else open_drop)
    except Exception:
        lu_drop = open_drop
    try:
        thr_clear_main = float(params.get("clear_gain_main") if params.get("clear_gain_main") is not None else 0.10)
    except Exception:
        thr_clear_main = 0.10
    try:
        thr_clear_growth = float(params.get("clear_gain_growth") if params.get("clear_gain_growth") is not None else 0.13)
    except Exception:
        thr_clear_growth = 0.13
    try:
        clear_drop_main = float(params.get("clear_drop_main") if params.get("clear_drop_main") is not None else 0.0)
    except Exception:
        clear_drop_main = 0.0
    try:
        clear_drop_growth = float(params.get("clear_drop_growth") if params.get("clear_drop_growth") is not None else 1.5)
    except Exception:
        clear_drop_growth = 1.5
    try:
        min_amt = float(params.get("min_order_amount") or 5000)
    except Exception:
        min_amt = 5000.0

    # positions state
    state = {}
    init = CFG.get("initial_positions") or {}
    for k, v in init.items():
        c6 = _code6(k)
        if not c6 or not isinstance(v, dict):
            continue
        try:
            vol = int(v.get("volume") or 0)
        except Exception:
            vol = 0
        if vol <= 0:
            continue
        ed = _parse_d(v.get("entry_date"))
        ld = _parse_d(v.get("last_buy_date")) or ed
        try:
            cost = float(v.get("cost") or 0)
        except Exception:
            cost = 0.0
        state[c6] = {
            "volume": vol,
            "cost": cost,
            "baseline": vol,
            "entry_date": ed,
            "last_buy_date": ld,
        }

    fb_cfg = CFG.get("first_buy_dates") or {}
    lb_cfg = CFG.get("last_buy_dates") or {}
    for c6, st in state.items():
        if st.get("entry_date") is None and fb_cfg.get(c6):
            st["entry_date"] = _parse_d(fb_cfg.get(c6))
        if st.get("last_buy_date") is None and lb_cfg.get(c6):
            st["last_buy_date"] = _parse_d(lb_cfg.get(c6))

    fills_by_day = {}
    for f in CFG.get("scheduled_buy_fills") or []:
        if not isinstance(f, dict):
            continue
        c6 = _code6(f.get("code") or f.get("stock_code"))
        bd = _parse_d(f.get("date") or f.get("trade_date"))
        if not c6 or bd is None:
            continue
        fills_by_day.setdefault(bd, []).append(f)

    codes = []
    for raw in CFG.get("codes") or []:
        c6 = _code6(raw)
        if c6:
            codes.append(c6)
    for c6 in list(state.keys()):
        if c6 not in codes:
            codes.append(c6)
    for bd, fl in fills_by_day.items():
        for f in fl:
            c6 = _code6(f.get("code"))
            if c6 and c6 not in codes:
                codes.append(c6)
    codes = sorted(set(codes))

    _emit("START", {
        "run_tag": run_tag,
        "start": start_d.isoformat(),
        "end": end_d.isoformat(),
        "codes": codes,
        "n_codes": len(codes),
        "from_t1": from_t1,
        "hold_n_engine": hold_n,
        "ui_n": ui_n,
        "clear_time": clear_time,
        "unicodedata": _UNICODEDATA_STATUS,
    })

    # download window: pad daily history for LU10 / prev_close
    dl_start = (start_d - timedelta(days=40)).strftime("%Y%m%d")
    dl_end = end_d.strftime("%Y%m%d")
    for c6 in codes:
        fc = _full_from_c6(c6)
        e1 = _call_download(ContextInfo, fc, "1d", dl_start, dl_end)
        e2 = _call_download(ContextInfo, fc, "tick", start_d.strftime("%Y%m%d"), dl_end)
        _log("download %s 1d=%s tick=%s" % (c6, e1 or "ok", e2 or "ok"))

    daily_cache = {}
    for c6 in codes:
        fc = _full_from_c6(c6)
        maps, yords, err = _daily_maps(ContextInfo, fc, dl_start, dl_end)
        daily_cache[c6] = (maps, yords, err)
        if err:
            _log("daily_map %s err=%s" % (c6, err))

    trading_days = _weekday_days(start_d, end_d)
    # shrink to days that appear in any daily map if available
    all_ymds = set()
    for c6 in codes:
        maps, yords, err = daily_cache.get(c6, ({}, [], ""))
        for y in yords:
            all_ymds.add(y)
    if all_ymds:
        filtered = []
        for d in trading_days:
            if d.strftime("%Y%m%d") in all_ymds:
                filtered.append(d)
        if filtered:
            trading_days = filtered

    trades = []
    skip_no_tick = 0
    filled_legs = set()  # LU10 whole window; OPEN50 keyed by day

    NAME_OPEN50 = "马总1卖-开盘涨幅弹性半仓"
    NAME_LU10 = "马总1卖-近10日涨停价弹性半仓"
    NAME_CLEAR_LU = "马总1卖-涨停即清仓"
    NAME_CLEAR_MAIN = "马总1卖-主板涨幅弹性清仓"
    NAME_CLEAR_GROWTH = "马总1卖-非主板涨幅弹性清仓"

    for day_d in trading_days:
        # inject scheduled buys for this day first
        for f in fills_by_day.get(day_d, []):
            c6 = _code6(f.get("code"))
            try:
                vol = int(f.get("volume") or 0)
            except Exception:
                vol = 0
            try:
                price = float(f.get("price") or 0)
            except Exception:
                price = 0.0
            blotter = bool(f.get("blotter_only"))
            _apply_buy_fill(state, c6, vol, price, day_d, blotter)

        for c6 in codes:
            st = state.get(c6)
            if not st or int(st.get("volume") or 0) < 100:
                continue
            hold = _full_lot(int(st.get("volume") or 0))
            avail = _available_qty(st, day_d, from_t1)
            base = _full_lot(int(st.get("baseline") or hold))
            if base < hold:
                base = hold
            if avail < 100:
                continue

            maps, yords, _derr = daily_cache.get(c6, ({}, [], ""))
            close_by = (maps or {}).get("close") or {}
            ymd = day_d.strftime("%Y%m%d")
            # prev close: last daily close strictly before today
            prev_close = 0.0
            for y in reversed(yords or []):
                if y < ymd:
                    try:
                        prev_close = float(close_by.get(y) or 0)
                    except Exception:
                        prev_close = 0.0
                    break
            if prev_close <= 0:
                _log("SKIP_NO_PREV_CLOSE|%s|%s" % (c6, ymd))
                continue

            lr = _limit_ratio(c6)
            limit_up = round(prev_close * (1.0 + lr), 2)
            limit_down = round(prev_close * (1.0 - lr), 2)

            fc = _full_from_c6(c6)
            ticks, terr = _ticks_for_day(ContextInfo, fc, day_d, run_start_sec, run_end_sec)
            if not ticks:
                skip_no_tick += 1
                _emit("SKIP_NO_TICK", {"code": c6, "date": day_d.isoformat(), "err": terr or "empty"})
                continue

            thr = open_gain_growth if _is_growth_board(c6) else open_gain_main
            open_ref = _clamp_px(round(prev_close * (1.0 + thr), 2), limit_down, limit_up) if prev_close > 0 else 0.0
            lu_raw = _recent_lu_trigger_from_daily(c6, close_by, yords, day_d)
            lu_ref = 0.0
            if lu_raw is not None and lu_raw > 0:
                lu_px = round(float(lu_raw), 2)
                if not (limit_up > 0 and lu_px > limit_up):
                    lu_ref = _clamp_px(lu_px, limit_down, limit_up)

            leg_open = "%s:OPEN50:%s" % (c6, ymd)
            leg_lu = "%s:LU10" % c6
            half = _half_lot(base)

            open_trig = float(open_ref) if (leg_open not in filled_legs and open_ref > 0 and half >= 100) else 0.0
            lu_trig = float(lu_ref) if (leg_lu not in filled_legs and lu_ref > 0 and half >= 100) else 0.0
            want_open = open_trig > 0
            want_lu = lu_trig > 0

            if want_open and want_lu:
                _a, _b = _half_pair_absorb_dust(base)
                _open50_filled = any(
                    str(k) == ("%s:OPEN50" % c6) or str(k).startswith("%s:OPEN50:" % c6)
                    for k in filled_legs
                )
                # remaining half: each leg sizes to full hold; first fill clears; other sees 0
                if _open50_filled or (_full_lot(hold) > 0 and _full_lot(hold) <= _b):
                    px_min_o = open_trig * (1.0 - float(open_drop) / 100.0)
                    px_min_l = lu_trig * (1.0 - float(lu_drop) / 100.0)
                    open_vol = _fold_dust(hold, hold, px_min_o, min_amt)
                    lu_vol = _fold_dust(hold, hold, px_min_l, min_amt)
                else:
                    open_vol, lu_vol = _a, _b
                    pair_cap = hold
                    if open_vol + lu_vol > pair_cap:
                        open_vol = min(open_vol, pair_cap)
                        lu_vol = max(0, pair_cap - open_vol)
                        if lu_vol < 100:
                            open_vol, lu_vol = pair_cap, 0
            elif want_open:
                px_min = open_trig * (1.0 - float(open_drop) / 100.0)
                open_vol = _fold_dust(half, hold, px_min, min_amt)
                lu_vol = 0
            elif want_lu:
                px_min = lu_trig * (1.0 - float(lu_drop) / 100.0)
                lu_vol = _fold_dust(half, hold, px_min, min_amt)
                open_vol = 0
            else:
                open_vol, lu_vol = 0, 0

            # intents with fill order: OPEN50 then LU10 then clear-up then scheduled
            day_intents = []
            if want_open and open_vol >= 100:
                day_intents.append({
                    "kind": "best_sell", "name": NAME_OPEN50, "leg": leg_open,
                    "trigger": open_trig, "drop": open_drop, "volume": int(open_vol),
                })
            if want_lu and lu_vol >= 100:
                day_intents.append({
                    "kind": "best_sell", "name": NAME_LU10, "leg": leg_lu,
                    "trigger": lu_trig, "drop": lu_drop, "volume": int(lu_vol),
                })

            # clear-up branch on avail
            if avail >= 100 and prev_close > 0:
                if _is_growth_board(c6):
                    if thr_clear_growth > 0:
                        ct = _clamp_px(round(prev_close * (1.0 + thr_clear_growth), 2), limit_down, limit_up)
                        if ct > 0:
                            day_intents.append({
                                "kind": "best_sell", "name": NAME_CLEAR_GROWTH, "leg": "%s:CLEAR_G:%s" % (c6, ymd),
                                "trigger": float(ct), "drop": clear_drop_growth, "volume": int(avail),
                                "is_clear": True,
                            })
                elif thr_clear_main > 0:
                    ct = _clamp_px(round(prev_close * (1.0 + thr_clear_main), 2), limit_down, limit_up)
                    if ct > 0:
                        lu_imm = abs(float(thr_clear_main) - 0.10) < 1e-9 and float(clear_drop_main) <= 1e-12
                        if lu_imm:
                            day_intents.append({
                                "kind": "single_sell", "name": NAME_CLEAR_LU, "leg": "%s:CLEAR_LU:%s" % (c6, ymd),
                                "trigger": float(ct), "drop": 0.0, "volume": int(avail),
                                "is_clear": True,
                            })
                        else:
                            day_intents.append({
                                "kind": "best_sell", "name": NAME_CLEAR_MAIN, "leg": "%s:CLEAR_M:%s" % (c6, ymd),
                                "trigger": float(ct), "drop": clear_drop_main, "volume": int(avail),
                                "is_clear": True,
                            })

            di = _sell_day_index(st, day_d, trading_days)
            if hold_n is not None and di == int(hold_n) and avail >= 100:
                clear_px = round(float(limit_up) - 0.01, 2) if limit_up > 0 else 0.0
                force = clear_px <= 0
                nm = "马总1卖-第%d日%s" % (ui_n, "无条件清仓" if force else "涨停外清仓")
                day_intents.append({
                    "kind": "scheduled_clear", "name": nm, "leg": "%s:SCLEAR:%s" % (c6, ymd),
                    "trigger": float(clear_px), "drop": 0.0, "volume": int(avail),
                    "force": force, "is_clear": True,
                })

            # replay in order; reduce avail/hold after each fill
            for intent in day_intents:
                hold = _full_lot(int(st.get("volume") or 0))
                avail = _available_qty(st, day_d, from_t1)
                if avail < 100 or hold < 100:
                    break
                vol = min(int(intent.get("volume") or 0), avail, hold)
                vol = _full_lot(vol)
                if vol < 100:
                    continue
                kind = intent.get("kind")
                fill = None
                if kind == "best_sell":
                    fill = _replay_best_sell(ticks, float(intent["trigger"]), float(intent["drop"]), run_start_sec, run_end_sec)
                elif kind == "single_sell":
                    fill = _replay_single_sell(ticks, float(intent["trigger"]), run_start_sec, run_end_sec)
                elif kind == "scheduled_clear":
                    fill = _replay_scheduled_clear(
                        ticks, float(intent.get("trigger") or 0), clear_sec,
                        bool(intent.get("force")), run_end_sec,
                    )
                if not fill:
                    continue
                sec, tm, px = fill
                if px <= 0:
                    continue
                amount = round(float(px) * vol, 2)
                st["volume"] = int(st.get("volume") or 0) - vol
                # baseline 不降（与实盘注释一致）
                if st["volume"] < 100:
                    st["volume"] = 0
                    st["baseline"] = 0
                leg = str(intent.get("leg") or "")
                filled_legs.add(leg)
                row = {
                    "code": c6,
                    "date": day_d.isoformat(),
                    "time": _fmt_tick_time(tm),
                    "side": "sell",
                    "price": round(float(px), 4),
                    "volume": int(vol),
                    "amount": amount,
                    "leg": leg,
                    "rule": intent.get("name") or "",
                    "trigger": intent.get("trigger"),
                    "drop": intent.get("drop"),
                    "selection_date": (st.get("entry_date").isoformat() if isinstance(st.get("entry_date"), date) else "") or "",
                }
                trades.append(row)
                _emit("TRADE", row)
                # clear legs consume all — stop further for this code/day
                if intent.get("is_clear"):
                    break

    _emit("SUMMARY", {
        "run_tag": run_tag,
        "trade_count": len(trades),
        "skip_no_tick": skip_no_tick,
        "codes": len(codes),
        "days": len(trading_days),
        "elapsed_sec": round(time.time() - t0, 2),
    })


def run_backtest(ContextInfo):
    """Entry for hot-reload probe (探针); also used by init for one-shot paste."""
    try:
        _run_backtest(ContextInfo)
    except Exception as e:
        _emit("ERROR", {"msg": str(e)[:500], "trace": traceback.format_exc()[:1500]})


def init(ContextInfo):
    run_backtest(ContextInfo)


def handlebar(ContextInfo):
    pass
