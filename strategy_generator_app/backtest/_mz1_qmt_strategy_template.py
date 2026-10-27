#coding:utf-8
"""马总逻辑1 细回测 · QMT 模型交易动态策略（由蚂蚁量化系统生成）

推荐：主程序「探针」热加载本文件（写到 userdata/mz1_qmt_fine_watch/current.py）。
亦可：模型交易 → 新建策略 → Python → 粘贴本文件 → 保存 → 启动（一次性）。
结果：print 到 userdata/log/XtClient_FormulaOutput_YYYYMMDD.log，前缀见 CFG.result_prefix
勿写 D:\\蚂蚁量化系统（Forbidden FileIO）；落盘仅尝试 QMT userdata。
Python 需 3.6 兼容；tick/日线只用大 QMT 内置 download_history_data + ContextInfo.get_market_data_ex(subscribe=False)；禁止 MiniQMT xtdata（58610）。
"""
from __future__ import print_function

import json
import time
import traceback
from datetime import date, datetime, timedelta

# ===== embedded config =====
CFG = json.loads(r"""___CFG_JSON___""")
PREFIX = CFG.get("result_prefix") or "ANT_MZ1_BT|"
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
    line = "%sLOG|%s" % (PREFIX, msg)
    try:
        print(line)
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
        return date(int(s[0:4]), int(s[5:7]), int(s[8:10]))
    except Exception:
        return None


def _code6(full_code):
    s = str(full_code or "").strip()
    if "." in s:
        s = s.split(".", 1)[0]
    if s.isdigit():
        return s.zfill(6)
    return s


def _resolve_fn(name):
    """Resolve QMT builtin APIs only (globals / builtins / __main__).

    Do NOT load MiniQMT xtdata modules (port 58610 / illegal-IP popup).
    Same rule as ant_shadow / ant_tick_cache_io / big-QMT migration notes.
    """
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
    """Fill local history via ContextInfo or builtin download_history_data.

    Official: download_history_data(code, period, YYYYMMDD, YYYYMMDD).
    Ban MiniQMT xtdata download path (port 58610).
    """
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
    """Read local bars via ContextInfo.get_market_data_ex(..., subscribe=False).

    Guojin binds this to get_market_data2; wrong kwargs (field_list/stock_list)
    raise Boost ArgumentError. Prefer official positional fields+stock_code.
    No fallback to MiniQMT xtdata (port 58610).
    """
    if ContextInfo is None:
        return None, "no_ContextInfo"
    fn = getattr(ContextInfo, "get_market_data_ex", None)
    if not callable(fn):
        return None, "no_ctx_get_market_data_ex"
    fields = []
    stocks = [full_code]
    last_err = ""
    # 1) Official style: positional fields + stock_code, kwargs for the rest
    try:
        data = fn(
            fields,
            stocks,
            period=period,
            start_time=start_s,
            end_time=end_s,
            count=-1,
            dividend_type="none",
            fill_data=False,
            subscribe=False,
        )
        return _extract_payload(data, full_code), ""
    except Exception as e:
        last_err = str(e)[:200]
    # 2) Fully positional (list stock)
    try:
        data = fn(fields, stocks, period, start_s, end_s, -1, "none", False, False)
        return _extract_payload(data, full_code), ""
    except Exception as e:
        last_err = str(e)[:200]
    # 3) Fully positional (single stock str) — matches some get_market_data2 bindings
    try:
        data = fn(fields, full_code, period, start_s, end_s, -1, "none", False, False)
        return _extract_payload(data, full_code), ""
    except Exception as e:
        last_err = str(e)[:200]
    # 4) Older get_market_data if present
    fn2 = getattr(ContextInfo, "get_market_data", None)
    if callable(fn2):
        try:
            data = fn2(
                fields,
                stock_code=stocks,
                start_time=start_s,
                end_time=end_s,
                skip_paused=True,
                period=period,
                dividend_type="none",
                count=-1,
            )
            return _extract_payload(data, full_code), ""
        except Exception as e:
            last_err = str(e)[:200]
    return None, last_err or "get_market_failed"


def _series(payload, *names):
    if payload is None:
        return []
    # get_market_data_ex returns pandas.DataFrame (columns=fields, index=time)
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
        return []
    if isinstance(payload, dict):
        for n in names:
            v = payload.get(n)
            if isinstance(v, (list, tuple)) and len(v) > 0:
                return list(v)
            # DataFrame-like values nested under field names
            try:
                if hasattr(v, "tolist"):
                    out = list(v.tolist())
                    if out:
                        return out
            except Exception:
                pass
        return []
    return []


def _as_ymd_list(ts_list):
    """Normalize bar timestamps to YYYYMMDD.

    QMT/pandas may give datetime, YYYYMMDD int, or epoch ms/ns (np.int64).
    Taking digits[:8] of a nanosecond stamp yields garbage like 17862912.
    """
    out = []
    for t in ts_list:
        try:
            if isinstance(t, datetime):
                out.append(t.strftime("%Y%m%d"))
                continue
            if isinstance(t, date):
                out.append(t.strftime("%Y%m%d"))
                continue
            # numpy.datetime64
            if type(t).__name__ == "datetime64" or hasattr(t, "dtype") and "datetime64" in str(getattr(t, "dtype", "")):
                try:
                    import pandas as pd
                    out.append(pd.Timestamp(t).strftime("%Y%m%d"))
                    continue
                except Exception:
                    pass
            # numeric epoch or YYYYMMDD
            try:
                v = float(t.item() if hasattr(t, "item") else t)
            except Exception:
                v = None
            if v is not None and v == v:  # not NaN
                if v >= 1e17:  # nanoseconds
                    dt = datetime.fromtimestamp(v / 1e9)
                    out.append(dt.strftime("%Y%m%d"))
                    continue
                if v >= 1e14:  # microseconds
                    dt = datetime.fromtimestamp(v / 1e6)
                    out.append(dt.strftime("%Y%m%d"))
                    continue
                if v >= 1e12:  # milliseconds
                    dt = datetime.fromtimestamp(v / 1e3)
                    out.append(dt.strftime("%Y%m%d"))
                    continue
                if v >= 1e9:  # seconds
                    dt = datetime.fromtimestamp(v)
                    out.append(dt.strftime("%Y%m%d"))
                    continue
                if v >= 1e7:  # YYYYMMDD as number
                    out.append(str(int(v))[:8])
                    continue
            s = str(t)
            digits = "".join(ch for ch in s if ch.isdigit())
            if len(digits) >= 17:  # ns as digit string
                dt = datetime.fromtimestamp(float(digits) / 1e9)
                out.append(dt.strftime("%Y%m%d"))
            elif len(digits) >= 14:  # YYYYMMDDhhmmss
                out.append(digits[:8])
            elif len(digits) >= 8:
                # only trust if looks like 19xx/20xx
                y = digits[:8]
                if y.startswith("19") or y.startswith("20"):
                    out.append(y)
                else:
                    out.append("")
            else:
                out.append("")
        except Exception:
            out.append("")
    return out


def _weekday_days(start_d, end_d):
    days = []
    d = start_d
    while d <= end_d:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _nth_weekday(start_d, n):
    if n <= 1:
        return start_d
    d = start_d
    counted = 0
    while counted < n:
        if d.weekday() < 5:
            counted += 1
            if counted == n:
                return d
        d += timedelta(days=1)
    return d


def _entry_bounds(sel_d, entry_window, from_t1):
    if sel_d is None:
        return None, None
    start = sel_d + timedelta(days=1)
    while start.weekday() >= 5:
        start += timedelta(days=1)
    if not from_t1:
        start = sel_d
        while start.weekday() >= 5:
            start += timedelta(days=1)
    end = _nth_weekday(start, max(1, int(entry_window)))
    return start, end


def _ma_from_daily(closes, idx, period):
    if idx is None or idx < 0 or idx + 1 < period:
        return None
    chunk = closes[idx + 1 - period : idx + 1]
    try:
        vals = [float(x) for x in chunk]
    except Exception:
        return None
    if len(vals) < period:
        return None
    return sum(vals) / float(period)


def _first_break_price(times, lasts, lows, trigger, rise_pct):
    if trigger is None or trigger <= 0:
        return None, None
    thr = float(trigger) * (1.0 - float(rise_pct or 0) / 100.0)
    n = min(len(lasts), len(times))
    use_low = bool(lows) and len(lows) >= n
    for i in range(n):
        try:
            last = float(lasts[i])
            px = float(lows[i]) if use_low else last
        except Exception:
            continue
        # QMT tick often has 0 placeholders before auction; never treat as touch
        if last <= 0 or px <= 0:
            continue
        if px <= thr:
            return last, times[i]
    return None, None


def _vol_for(amount, price, min_order):
    if price is None or price <= 0:
        return 0
    px = float(price)
    v = max(100, int(float(amount) / px / 100.0) * 100)
    # bump to clear min_order (100-share lot) instead of dropping the fill
    need = int(float(min_order) / px / 100.0) * 100
    if need * px < float(min_order):
        need += 100
    if need > v:
        v = need
    if v <= 0 or v * px < float(min_order):
        return 0
    return v


def _fmt_tick_time(tm):
    """Tick stamp -> HH:MM:SS (QMT often gives epoch ms/ns)."""
    if tm is None:
        return ""
    if isinstance(tm, datetime):
        return tm.strftime("%H:%M:%S")
    try:
        v = float(tm.item() if hasattr(tm, "item") else tm)
    except Exception:
        v = None
    if v is not None and v == v and v > 0:
        try:
            if v >= 1e17:  # ns
                return datetime.fromtimestamp(v / 1e9).strftime("%H:%M:%S")
            if v >= 1e14:  # us
                return datetime.fromtimestamp(v / 1e6).strftime("%H:%M:%S")
            if v >= 1e12:  # ms
                return datetime.fromtimestamp(v / 1e3).strftime("%H:%M:%S")
            if v >= 1e9:  # sec
                return datetime.fromtimestamp(v).strftime("%H:%M:%S")
            # YYYYMMDDhhmmss or hhmmss
            s = str(int(v))
            if len(s) >= 14:
                return "%s:%s:%s" % (s[-6:-4], s[-4:-2], s[-2:])
            if len(s) == 6:
                return "%s:%s:%s" % (s[0:2], s[2:4], s[4:6])
        except Exception:
            pass
    s = str(tm).strip()
    if not s:
        return ""
    # already HH:MM:SS
    if len(s) >= 8 and s[2] == ":" and s[5] == ":":
        return s[:8]
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 14:
        return "%s:%s:%s" % (digits[-6:-4], digits[-4:-2], digits[-2:])
    if len(digits) >= 12:  # epoch ms as digits
        try:
            return datetime.fromtimestamp(float(digits[:13]) / 1e3).strftime("%H:%M:%S")
        except Exception:
            pass
    return s


def _daily_maps(ContextInfo, full_code, daily_start, end_s):
    """Return (ymds_ord, closes_ord, low_by_ymd) ascending by ymd."""
    daily, err = _get_market(ContextInfo, full_code, "1d", daily_start, end_s)
    d_times = _series(daily, "time", "stamp")
    d_close = _series(daily, "close", "Close", "lastPrice")
    d_low = _series(daily, "low", "Low")
    d_ymd = _as_ymd_list(d_times)
    close_by_ymd = {}
    low_by_ymd = {}
    for i, y in enumerate(d_ymd):
        if not y:
            continue
        if i < len(d_close):
            try:
                close_by_ymd[y] = float(d_close[i])
            except Exception:
                pass
        if i < len(d_low):
            try:
                low_by_ymd[y] = float(d_low[i])
            except Exception:
                pass
    ymds_ord = sorted(close_by_ymd.keys())
    closes_ord = [close_by_ymd[y] for y in ymds_ord]
    return ymds_ord, closes_ord, low_by_ymd, err, daily


def _idx_before_ymd(ymds_ord, ymd):
    """Largest index with ymds_ord[i] < ymd (exclusive today for morning MA)."""
    idx = None
    for i, y in enumerate(ymds_ord):
        if y >= ymd:
            idx = i - 1
            break
    if idx is None and ymds_ord and ymds_ord[-1] < ymd:
        idx = len(ymds_ord) - 1
    if idx is not None and idx < 0:
        idx = None
    return idx


def _already_touched_leg(ymds_ord, closes_ord, low_by_ymd, sel_d, entry_window, from_t1, before_d, ma_period):
    """选股日后挂单窗内、before_d 之前（不含当日）日线是否已跌破早盘 MA。"""
    if sel_d is None or before_d is None:
        return False
    e0, e1 = _entry_bounds(sel_d, entry_window, from_t1)
    if e0 is None:
        return False
    period = max(2, int(ma_period or 10))
    need_prior = period - 1
    d = e0
    while d < before_d and d <= e1:
        if d.weekday() < 5:
            ymd = d.strftime("%Y%m%d")
            idx = _idx_before_ymd(ymds_ord, ymd)
            if idx is not None and idx + 1 >= need_prior:
                # align first_ma_touch: morning N-day = prior N-1 closes
                ma_px = _ma_from_daily(closes_ord, idx, need_prior)
                low_v = low_by_ymd.get(ymd)
                if ma_px is not None and low_v is not None and low_v > 0 and low_v <= ma_px:
                    return True
        d += timedelta(days=1)
    return False


def _run_backtest(ContextInfo):
    codes = list(CFG.get("codes") or [])
    sel_map = CFG.get("selection_date_by_code") or {}
    daily_pools = CFG.get("daily_pools") or {}
    start_d = _parse_d(CFG.get("start"))
    end_d = _parse_d(CFG.get("end"))
    entry_window = int(CFG.get("entry_window_trading_days") or 10)
    amount_per = float(CFG.get("buy_amount_per_stock") or 50000)
    min_order = float(CFG.get("min_order_amount") or 5000)
    rise_pct = float(CFG.get("rise_percent") or 0)
    from_t1 = bool(CFG.get("from_t1", True))
    half = amount_per / 2.0

    if start_d is None or end_d is None:
        _emit("ERROR", {"msg": "bad start/end"})
        return

    start_s = start_d.strftime("%Y%m%d")
    end_s = end_d.strftime("%Y%m%d")
    daily_start = (start_d - timedelta(days=60)).strftime("%Y%m%d")

    _emit("START", {
        "run_tag": CFG.get("run_tag"),
        "start": CFG.get("start"),
        "end": CFG.get("end"),
        "n_codes": len(codes),
        "n_daily_pools": len(daily_pools) if isinstance(daily_pools, dict) else 0,
        "mode": "daily_pools" if daily_pools else "static_pool",
        "unicodedata": _UNICODEDATA_STATUS,
    })

    # union codes for download
    dl_codes = list(codes)
    if isinstance(daily_pools, dict):
        seen = set(_code6(c) for c in dl_codes)
        for _day_key, info in daily_pools.items():
            if not isinstance(info, dict):
                continue
            for c in (info.get("codes") or []):
                c6 = _code6(c)
                if c6 and c6 not in seen:
                    seen.add(c6)
                    if "." in str(c):
                        dl_codes.append(str(c))
                    else:
                        # rebuild full code later via download loop using SH/SZ heuristic in CFG codes
                        if c6.startswith(("5", "6", "9")):
                            dl_codes.append(c6 + ".SH")
                        else:
                            dl_codes.append(c6 + ".SZ")

    for full_code in dl_codes:
        err = _call_download(ContextInfo, full_code, "1d", daily_start, end_s)
        if err:
            _log("download 1d %s %s" % (full_code, err))
        err = _call_download(ContextInfo, full_code, "tick", start_s, end_s)
        if err:
            _log("download tick %s %s" % (full_code, err))
        time.sleep(0.05)

    filled = set(str(x) for x in (CFG.get("filled_legs") or []) if x)
    trades = []
    days = _weekday_days(start_d, end_d)
    skip = {"no_ma": 0, "no_last": 0, "no_break": 0, "no_vol": 0, "days_checked": 0, "already": 0, "out_window": 0}

    # cache daily maps per full_code
    daily_cache = {}

    def _full_from_c6(c6):
        c6 = _code6(c6)
        if c6.startswith(("5", "6", "9")):
            return c6 + ".SH"
        return c6 + ".SZ"

    def _ensure_daily(full_code):
        if full_code in daily_cache:
            return daily_cache[full_code]
        ymds_ord, closes_ord, low_by_ymd, err, _daily = _daily_maps(
            ContextInfo, full_code, daily_start, end_s
        )
        daily_cache[full_code] = (ymds_ord, closes_ord, low_by_ymd, err)
        return daily_cache[full_code]

    use_daily_pools = bool(isinstance(daily_pools, dict) and daily_pools)

    if use_daily_pools:
        for day in days:
            day_key = day.isoformat()
            info = daily_pools.get(day_key) or daily_pools.get(day.strftime("%Y-%m-%d"))
            if not isinstance(info, dict):
                continue
            day_codes = list(info.get("codes") or [])
            day_sel = info.get("selection_date_by_code") or {}
            if not day_codes:
                continue
            for raw_code in day_codes:
                full_code = str(raw_code)
                if "." not in full_code:
                    full_code = _full_from_c6(full_code)
                c6 = _code6(full_code)
                sel_raw = day_sel.get(c6) or day_sel.get(full_code) or day_sel.get(raw_code)
                sel_d = _parse_d(sel_raw) if sel_raw else None
                if sel_d:
                    e0, e1 = _entry_bounds(sel_d, entry_window, from_t1)
                else:
                    e0, e1 = start_d, end_d
                if e0 is None:
                    e0, e1 = start_d, end_d
                if day < e0 or day > e1:
                    skip["out_window"] += 1
                    continue

                ymds_ord, closes_ord, low_by_ymd, err = _ensure_daily(full_code)
                ymd = day.strftime("%Y%m%d")
                idx = _idx_before_ymd(ymds_ord, ymd)
                ma10 = _ma_from_daily(closes_ord, idx, 10)
                ma20 = _ma_from_daily(closes_ord, idx, 20)

                tick, terr = _get_market(ContextInfo, full_code, "tick", ymd, ymd)
                if terr or _nrows(tick) <= 0:
                    continue
                t_times = _series(tick, "time", "stamp")
                t_last = _series(tick, "lastPrice", "last", "close", "LastPrice", "price")
                t_low = _series(tick, "low", "Low")
                if not t_last:
                    skip["no_last"] += 1
                    continue

                for leg, ma_px, leg_name, ma_period in (
                    ("MA10", ma10, "马总1-跌破MA10", 10),
                    ("MA20", ma20, "马总1-跌破MA20", 20),
                ):
                    key = "%s:%s" % (c6, leg)
                    if key in filled:
                        continue
                    if _already_touched_leg(
                        ymds_ord, closes_ord, low_by_ymd, sel_d, entry_window, from_t1, day, ma_period
                    ):
                        skip["already"] += 1
                        continue
                    if ma_px is None:
                        skip["no_ma"] += 1
                        continue
                    skip["days_checked"] += 1
                    fill_px, fill_tm = _first_break_price(
                        t_times, t_last, t_low, ma_px, rise_pct
                    )
                    if fill_px is None:
                        skip["no_break"] += 1
                        continue
                    vol = _vol_for(half, fill_px, min_order)
                    if vol <= 0:
                        skip["no_vol"] += 1
                        continue
                    filled.add(key)
                    row = {
                        "code": c6,
                        "full_code": full_code,
                        "leg": leg,
                        "rule": leg_name,
                        "date": day.isoformat(),
                        "time": _fmt_tick_time(fill_tm),
                        "trigger": round(float(ma_px), 4),
                        "price": round(float(fill_px), 4),
                        "volume": int(vol),
                        "amount": round(float(fill_px) * vol, 2),
                        "selection_date": sel_d.isoformat() if sel_d else "",
                    }
                    trades.append(row)
                    _emit("TRADE", row)
    else:
        # legacy: static pool + entry window scan
        for full_code in codes:
            c6 = _code6(full_code)
            sel_raw = sel_map.get(c6) or sel_map.get(full_code)
            sel_d = _parse_d(sel_raw) if sel_raw else None
            if sel_d:
                e0, e1 = _entry_bounds(sel_d, entry_window, from_t1)
            else:
                e0, e1 = start_d, end_d
            if e0 is None:
                e0, e1 = start_d, end_d

            ymds_ord, closes_ord, low_by_ymd, err = _ensure_daily(full_code)
            for day in days:
                if day < e0 or day > e1:
                    continue
                ymd = day.strftime("%Y%m%d")
                idx = _idx_before_ymd(ymds_ord, ymd)
                ma10 = _ma_from_daily(closes_ord, idx, 10)
                ma20 = _ma_from_daily(closes_ord, idx, 20)

                tick, terr = _get_market(ContextInfo, full_code, "tick", ymd, ymd)
                if terr or _nrows(tick) <= 0:
                    continue
                t_times = _series(tick, "time", "stamp")
                t_last = _series(tick, "lastPrice", "last", "close", "LastPrice", "price")
                t_low = _series(tick, "low", "Low")
                if not t_last:
                    skip["no_last"] += 1
                    continue

                for leg, ma_px, leg_name, ma_period in (
                    ("MA10", ma10, "马总1-跌破MA10", 10),
                    ("MA20", ma20, "马总1-跌破MA20", 20),
                ):
                    key = "%s:%s" % (c6, leg)
                    if key in filled:
                        continue
                    if _already_touched_leg(
                        ymds_ord, closes_ord, low_by_ymd, sel_d, entry_window, from_t1, day, ma_period
                    ):
                        skip["already"] += 1
                        continue
                    if ma_px is None:
                        skip["no_ma"] += 1
                        continue
                    skip["days_checked"] += 1
                    fill_px, fill_tm = _first_break_price(
                        t_times, t_last, t_low, ma_px, rise_pct
                    )
                    if fill_px is None:
                        skip["no_break"] += 1
                        continue
                    vol = _vol_for(half, fill_px, min_order)
                    if vol <= 0:
                        skip["no_vol"] += 1
                        continue
                    filled.add(key)
                    row = {
                        "code": c6,
                        "full_code": full_code,
                        "leg": leg,
                        "rule": leg_name,
                        "date": day.isoformat(),
                        "time": _fmt_tick_time(fill_tm),
                        "trigger": round(float(ma_px), 4),
                        "price": round(float(fill_px), 4),
                        "volume": int(vol),
                        "amount": round(float(fill_px) * vol, 2),
                        "selection_date": sel_d.isoformat() if sel_d else "",
                    }
                    trades.append(row)
                    _emit("TRADE", row)

    _emit("SUMMARY", {
        "run_tag": CFG.get("run_tag"),
        "trade_count": len(trades),
        "filled_legs": sorted(filled),
        "start": CFG.get("start"),
        "end": CFG.get("end"),
        "skip": skip,
    })
    _log("done trades=%d" % len(trades))



def run_backtest(ContextInfo):
    """Entry for hot-reload probe (探针); also used by init for one-shot paste."""
    _run_backtest(ContextInfo)


def init(ContextInfo):
    try:
        run_backtest(ContextInfo)
    except Exception:
        _emit("ERROR", {"msg": traceback.format_exc()[-1500:]})


def handlebar(ContextInfo):
    return
