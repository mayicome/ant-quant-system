#coding:utf-8
"""马总逻辑1卖 / 盘中买入次日了结 / 盘中买入冲高卖出 细回测 · QMT 模型交易动态策略（由蚂蚁量化系统生成）

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
# 同一 run_tag 只跑一次，避免探针重复触发把 QMT 打崩
_DONE_RUN_TAGS = set()


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


def _thirds_absorb_dust(total):
    total = _full_lot(total)
    if total < 100:
        return 0, 0, 0
    lots = total // 100
    a = (lots // 3) * 100
    b = (lots // 3) * 100
    c = total - a - b
    return a, b, c


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




def _min_tick_prec(c6):
    c6 = _code6(c6)
    # ETF/基金常见 0.001；其余股票 0.01
    if c6.startswith(("15", "16", "18", "50", "51", "56", "58")):
        return 0.001, 3
    return 0.01, 2


def _replay_breakthrough_sell(ticks, price, run_start_sec, run_end_sec, after_sec=None):
    """现价/最低 < 触发价（向下突破）成交。"""
    if price <= 0 or not ticks:
        return None
    for sec, tm, last, hi, lo in ticks:
        if after_sec is not None and sec < after_sec:
            continue
        if run_start_sec is not None and sec < run_start_sec:
            continue
        if run_end_sec is not None and sec > run_end_sec:
            continue
        use_lo = lo if lo > 0 else last
        if last + 1e-9 < price or use_lo + 1e-9 < price:
            return (sec, tm, last if last > 0 else use_lo)
    return None


def _resolve_clear_branch(ticks, branch_sec, limit_up):
    """到 T 点首个 tick：贴涨停视为封板。返回 (resolved, sealed, after_sec)。"""
    if not ticks or limit_up <= 0:
        return True, False, branch_sec
    for sec, tm, last, hi, lo in ticks:
        if sec < branch_sec:
            continue
        sealed = bool(last + 1e-9 >= float(limit_up) * 0.999)
        return True, sealed, sec
    # 整天无 T 后 tick：按未封板处理，允许原清仓（与墙钟补结算口径接近）
    return True, False, branch_sec

def _weekday_days(start_d, end_d):
    out = []
    d = start_d
    while d <= end_d:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _recent_lu_trigger_from_daily(c6, closes_by_ymd, highs_by_ymd, ymds_ord, as_d, sel_d, offset=0.01):
    """选股日涨停板起至昨天：日线最高价最大值减去固定价差。"""
    if not ymds_ord or sel_d is None:
        return None
    as_s = as_d.strftime("%Y%m%d")
    if hasattr(sel_d, "strftime"):
        sel_s = sel_d.strftime("%Y%m%d")
    else:
        sel_s = str(sel_d).replace("-", "")[:8]
    if not sel_s or sel_s >= as_s:
        return None
    prevs = [y for y in ymds_ord if y and y < sel_s]
    if not prevs:
        return None
    # QMT 日线可能升序或降序；取严格小于选股日的最近一日
    y_prev = max(prevs)
    if sel_s not in closes_by_ymd:
        return None
    prev_c = float(closes_by_ymd.get(y_prev) or 0)
    cur_c = float(closes_by_ymd.get(sel_s) or 0)
    if prev_c <= 0 or cur_c <= 0:
        return None
    lr = _limit_ratio(c6)
    lim = round(prev_c * (1.0 + lr), 2)
    inc = (cur_c - prev_c) / prev_c
    if not (abs(cur_c - lim) < 0.02 or inc >= lr * 0.99):
        return None
    window = [y for y in ymds_ord if sel_s <= y < as_s]
    if not window:
        return None
    highs = []
    for y in window:
        try:
            h = float(highs_by_ymd.get(y) or 0)
        except Exception:
            h = 0.0
        if h > 0:
            highs.append(h)
    if not highs:
        return None
    try:
        off = float(offset)
    except (TypeError, ValueError):
        off = 0.01
    if off < 0:
        off = 0.0
    return float(round(max(highs) - off, 2))
def _row_get(row, *names):
    for n in names:
        try:
            if hasattr(row, "get"):
                if n in row:
                    return row.get(n)
            elif hasattr(row, "__contains__") and n in row:
                return row[n]
        except Exception:
            pass
        try:
            return row[n]
        except Exception:
            pass
    return None


def _daily_maps(ContextInfo, full_code, start_s, end_s):
    payload, err = _get_market(ContextInfo, full_code, "1d", start_s, end_s)
    if err or payload is None:
        return {}, [], err or "no_daily"
    close_by = {}
    open_by = {}
    high_by = {}
    low_by = {}
    preclose_by = {}
    ord_ymds = []

    # DataFrame：按行取，避免 time/close 两列错位；并读 preClose
    if hasattr(payload, "iterrows"):
        try:
            for idx, row in payload.iterrows():
                y = _tick_ymd(idx)
                if not y:
                    y = _tick_ymd(_row_get(row, "time", "stamp", "stime"))
                if not y:
                    continue
                ord_ymds.append(y)
                try:
                    close_by[y] = float(_row_get(row, "close", "Close", "lastPrice", "last") or 0)
                except Exception:
                    pass
                try:
                    open_by[y] = float(_row_get(row, "open", "Open") or 0)
                except Exception:
                    pass
                try:
                    high_by[y] = float(_row_get(row, "high", "High") or 0)
                except Exception:
                    pass
                try:
                    low_by[y] = float(_row_get(row, "low", "Low") or 0)
                except Exception:
                    pass
                try:
                    pc = _row_get(row, "preClose", "lastClose", "pre_close", "PreClose")
                    if pc is not None:
                        preclose_by[y] = float(pc or 0)
                except Exception:
                    pass
        except Exception:
            ord_ymds = []

    if not ord_ymds:
        times = _series(payload, "time", "stamp", "stime")
        closes = _series(payload, "close", "Close", "lastPrice", "last")
        opens = _series(payload, "open", "Open")
        highs = _series(payload, "high", "High")
        lows = _series(payload, "low", "Low")
        precloses = _series(payload, "preClose", "lastClose", "pre_close", "PreClose")
        ymds = _as_ymd_list(times)
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
            try:
                if precloses and i < len(precloses):
                    preclose_by[y] = float(precloses[i] or 0)
            except Exception:
                pass

    # QMT 返回顺序不保证升序；统一成升序去重，供 prev_close / LU10 窗口使用
    seen = set()
    uniq = []
    for y in ord_ymds:
        if y and y not in seen:
            seen.add(y)
            uniq.append(y)
    ord_ymds = sorted(uniq)
    return {
        "close": close_by,
        "open": open_by,
        "high": high_by,
        "low": low_by,
        "preclose": preclose_by,
    }, ord_ymds, ""


def _ticks_for_day(ContextInfo, full_code, day_d, run_start_sec, run_end_sec):
    day_s = day_d.strftime("%Y%m%d")
    payload, err = _get_market(ContextInfo, full_code, "tick", day_s, day_s)
    meta = {"pre_close": 0.0, "limit_up": 0.0, "limit_down": 0.0}
    if err or payload is None or _nrows(payload) <= 0:
        return [], err or "no_tick", meta
    times = _series(payload, "time", "stamp")
    lasts = _series(payload, "lastPrice", "last", "close", "Close")
    highs = _series(payload, "high", "High")
    lows = _series(payload, "low", "Low")
    precloses = _series(payload, "lastClose", "preClose", "pre_close", "last_close")
    limit_ups = _series(payload, "highLimit", "upperLimit", "limitUp", "HighLimit")
    limit_dns = _series(payload, "lowLimit", "lowerLimit", "limitDown", "LowLimit")
    if not times or not lasts:
        return [], "empty_tick_fields", meta
    # 昨收/涨停只扫前几十笔，避免整天 tick 上重复解析把内存打爆
    if precloses:
        for i in range(min(64, len(precloses))):
            try:
                pc = float(precloses[i] or 0)
            except Exception:
                pc = 0.0
            if pc > 0:
                meta["pre_close"] = pc
                break
    if limit_ups:
        for i in range(min(64, len(limit_ups))):
            try:
                lu = float(limit_ups[i] or 0)
            except Exception:
                lu = 0.0
            if lu > 0:
                meta["limit_up"] = lu
                break
    if limit_dns:
        for i in range(min(64, len(limit_dns))):
            try:
                ld = float(limit_dns[i] or 0)
            except Exception:
                ld = 0.0
            if ld > 0:
                meta["limit_down"] = ld
                break
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
    return out, "", meta


def _px_at_generation(ticks, gen_sec):
    """对齐实盘：策略生成时刻最新价。
    生成时间多为 HH:mm → 取该分钟内最后一笔；没有则用之后第一笔。
    """
    if not ticks:
        return 0.0
    try:
        g = int(gen_sec) if gen_sec is not None else None
    except Exception:
        g = None
    if g is None:
        try:
            return float(ticks[0][2] or 0)
        except Exception:
            return 0.0
    # HH:mm 无秒时 gen_sec 落在整分；窗口含该分钟 0～59 秒
    g_end = g + 59
    before = None
    after = None
    for sec, tm, last, hi, lo in ticks:
        try:
            px = float(last or 0)
        except Exception:
            px = 0.0
        if px <= 0:
            continue
        if sec <= g_end:
            before = px
        elif after is None:
            after = px
            break
    if before and before > 0:
        return float(before)
    if after and after > 0:
        return float(after)
    try:
        return float(ticks[0][2] or 0)
    except Exception:
        return 0.0


def _ensure_lots(st):
    """兼容旧仓位：无 lots 时按 entry/last_buy 合成一批。"""
    lots = st.get("lots")
    if isinstance(lots, list):
        return lots
    lots = []
    vol = int(st.get("volume") or 0)
    if vol > 0:
        ed = st.get("entry_date")
        if isinstance(ed, str):
            ed = _parse_d(ed)
        ld = st.get("last_buy_date")
        if isinstance(ld, str):
            ld = _parse_d(ld)
        # 无分笔信息时整笔记在最早买入日，避免「末日加仓」锁死先买仓
        bd = ed or ld
        if bd is not None:
            lots.append({"date": bd, "volume": int(vol)})
    st["lots"] = lots
    return lots


def _add_lot(st, buy_d, vol):
    vol = int(vol)
    if vol <= 0 or buy_d is None:
        return
    lots = _ensure_lots(st)
    for lot in lots:
        ld = lot.get("date")
        if isinstance(ld, str):
            ld = _parse_d(ld)
            lot["date"] = ld
        if ld == buy_d:
            lot["volume"] = int(lot.get("volume") or 0) + vol
            return
    lots.append({"date": buy_d, "volume": vol})


def _consume_available_lots(st, vol, day_d, from_t1):
    """卖出时按 T+1 可卖批次 FIFO 扣减 lots，并同步 volume。"""
    vol = _full_lot(int(vol))
    if vol < 100:
        return 0
    lots = _ensure_lots(st)
    left = vol
    for lot in lots:
        if left < 100:
            break
        ld = lot.get("date")
        if isinstance(ld, str):
            ld = _parse_d(ld)
            lot["date"] = ld
        if from_t1 and (ld is None or not (ld < day_d)):
            continue
        have = _full_lot(int(lot.get("volume") or 0))
        if have < 100:
            continue
        take = min(have, left)
        take = _full_lot(take)
        if take < 100:
            continue
        lot["volume"] = int(lot.get("volume") or 0) - take
        left -= take
    st["lots"] = [x for x in lots if int(x.get("volume") or 0) > 0]
    taken = vol - left
    st["volume"] = max(0, int(st.get("volume") or 0) - taken)
    return taken


def _apply_buy_fill(state, c6, vol, price, buy_d, blotter_only):
    st = state.setdefault(c6, {
        "volume": 0, "cost": 0.0, "baseline": 0,
        "entry_date": None, "last_buy_date": None,
        "lots": [],
    })
    _ensure_lots(st)
    vol = int(vol)
    if vol <= 0:
        return
    if blotter_only:
        # already in initial_positions; only refresh last_buy if older missing
        if st.get("last_buy_date") is None:
            st["last_buy_date"] = buy_d
        # 补记批次（若初始仓尚未按日拆分）
        if buy_d is not None and not st.get("lots"):
            _add_lot(st, buy_d, int(st.get("volume") or 0))
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
    _add_lot(st, buy_d, vol)


def _available_qty(st, day_d, from_t1):
    hold = _full_lot(int(st.get("volume") or 0))
    if hold < 100:
        return 0
    if not from_t1:
        return hold
    # T+1：按买入批次，仅「买入日 < 今日」的仓可卖（当日加仓不锁死先买仓）
    lots = _ensure_lots(st)
    avail = 0
    for lot in lots:
        ld = lot.get("date")
        if isinstance(ld, str):
            ld = _parse_d(ld)
        if ld is None or not (ld < day_d):
            continue
        avail += _full_lot(int(lot.get("volume") or 0))
    return _full_lot(min(hold, avail))


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
    gen_sec = _parse_hms(CFG.get("strategy_generation_time") or "09:25") or (9 * 3600 + 25 * 60)
    # tick 需覆盖生成时刻（可早于运行开始），成交仍由 run_start_sec 过滤
    tick_load_from_sec = run_start_sec
    if gen_sec is not None and (tick_load_from_sec is None or gen_sec < tick_load_from_sec):
        tick_load_from_sec = gen_sec

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
        try:
            lu_high_offset = float(params.get("lu_high_offset") if params.get("lu_high_offset") is not None else 0.01)
        except (TypeError, ValueError):
            lu_high_offset = 0.01
        if lu_high_offset < 0:
            lu_high_offset = 0.0
    except Exception:
        lu_drop = open_drop
        lu_high_offset = 0.01
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
            "lots": [{"date": ed or ld, "volume": vol}] if (ed or ld) else [],
        }

    fb_cfg = CFG.get("first_buy_dates") or {}
    lb_cfg = CFG.get("last_buy_dates") or {}
    for c6, st in state.items():
        if st.get("entry_date") is None and fb_cfg.get(c6):
            st["entry_date"] = _parse_d(fb_cfg.get(c6))
        if st.get("last_buy_date") is None and lb_cfg.get(c6):
            st["last_buy_date"] = _parse_d(lb_cfg.get(c6))
        _ensure_lots(st)
        # 若只有总量、lots 空：按最早买入日记一批，避免末日加仓锁仓
        if int(st.get("volume") or 0) > 0 and not st.get("lots"):
            bd = st.get("entry_date") or st.get("last_buy_date")
            if bd is not None:
                st["lots"] = [{"date": bd, "volume": int(st.get("volume") or 0)}]

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
        "sell_variant": str(CFG.get("sell_variant") or "mz1"),
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

    variant = str(CFG.get("sell_variant") or "mz1").strip() or "mz1"
    sel_map = CFG.get("selection_date_by_code") or {}
    if not isinstance(sel_map, dict) or not sel_map:
        _sp_sel = (CFG.get("params") or {}).get("selection_date_by_code") or {}
        if isinstance(_sp_sel, dict) and _sp_sel:
            sel_map = _sp_sel
    if not isinstance(sel_map, dict):
        sel_map = {}
    is_ind = variant == "intraday_next_day"
    is_surge = variant == "intraday_surge"

    NAME_OPEN50 = "马总1卖-开盘涨幅弹性半仓"
    NAME_LU10 = "马总1卖-涨停后最高价下半仓"
    NAME_OPEN_PULL = "盘中次日了结-当前价回撤弹性全仓"
    NAME_SURGE4 = "盘中冲高卖出-涨幅4%三分之一"
    NAME_SURGE6 = "盘中冲高卖出-涨幅6%三分之一"
    NAME_SURGE8 = "盘中冲高卖出-涨幅8%三分之一"
    if is_surge:
        # 冲高卖出无涨停即清/弹性清仓腿；仅保留 sc_prefix 供末日定时清仓命名
        sc_prefix = "盘中冲高卖出"
        NAME_CLEAR_LU = NAME_CLEAR_MAIN = NAME_CLEAR_GROWTH = ""
    elif is_ind:
        NAME_CLEAR_LU = "盘中次日了结-涨停即清仓"
        NAME_CLEAR_MAIN = "盘中次日了结-主板涨幅弹性清仓"
        NAME_CLEAR_GROWTH = "盘中次日了结-非主板涨幅弹性清仓"
        sc_prefix = "盘中次日了结"
    else:
        NAME_CLEAR_LU = "马总1卖-涨停即清仓"
        NAME_CLEAR_MAIN = "马总1卖-主板涨幅弹性清仓"
        NAME_CLEAR_GROWTH = "马总1卖-非主板涨幅弹性清仓"
        sc_prefix = "马总1卖"

    # 当前价回撤全仓：优先 open_drop_percent（常量名保留）
    try:
        open_pull_drop = float(
            params.get("open_drop_percent")
            if params.get("open_drop_percent") is not None
            else (open_drop if open_drop is not None else 2.0)
        )
    except Exception:
        open_pull_drop = 2.0
    # 允许 0.0：触价即成（与 _replay_best_sell / 实盘 _leg_drop 一致）；负值才回退默认
    if open_pull_drop < 0:
        open_pull_drop = 2.0

    branch_at_s = str(
        CFG.get("clear_branch_at")
        or params.get("clear_branch_at")
        or "09:50:00"
    ).strip() or "09:50:00"
    branch_sec = _parse_hms(branch_at_s) or (9 * 3600 + 50 * 60)

    # 模板内 clear_drop_* 已读；盘中策略 JSON 别名再兜一层
    try:
        if params.get("clear_drop_percent_main") is not None:
            clear_drop_main = float(params.get("clear_drop_percent_main"))
    except Exception:
        pass
    try:
        if params.get("clear_drop_percent") is not None and (is_ind or is_surge):
            clear_drop_growth = float(params.get("clear_drop_percent"))
    except Exception:
        pass

    try:
        surge_g4 = float(params.get("surge_gain_4") if params.get("surge_gain_4") is not None else 0.04)
    except Exception:
        surge_g4 = 0.04
    try:
        surge_g6 = float(params.get("surge_gain_6") if params.get("surge_gain_6") is not None else 0.06)
    except Exception:
        surge_g6 = 0.06
    try:
        surge_g8 = float(params.get("surge_gain_8") if params.get("surge_gain_8") is not None else 0.08)
    except Exception:
        surge_g8 = 0.08
    if surge_g4 < 0:
        surge_g4 = 0.04
    if surge_g6 < 0:
        surge_g6 = 0.06
    if surge_g8 < 0:
        surge_g8 = 0.08

    _emit("LOG", "sell_variant=%s is_ind=%s is_surge=%s branch_at=%s gen=%s run_start=%s open_pull_drop=%s surge=%s/%s/%s" % (
        variant, is_ind, is_surge, branch_at_s,
        CFG.get("strategy_generation_time"), CFG.get("strategy_run_start_time"),
        open_pull_drop, surge_g4, surge_g6, surge_g8,
    ))

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
            open_by = (maps or {}).get("open") or {}
            high_by = (maps or {}).get("high") or {}
            preclose_by = (maps or {}).get("preclose") or {}
            ymd = day_d.strftime("%Y%m%d")
            lr = _limit_ratio(c6)
            tick_sz, prec = _min_tick_prec(c6)

            fc = _full_from_c6(c6)
            ticks, terr, tick_meta = _ticks_for_day(
                ContextInfo, fc, day_d, tick_load_from_sec, run_end_sec
            )
            if not ticks:
                skip_no_tick += 1
                _emit("SKIP_NO_TICK", {"code": c6, "date": day_d.isoformat(), "err": terr or "empty"})
                continue
            if not isinstance(tick_meta, dict):
                tick_meta = {}

            # 昨收优先级：当日 tick.lastClose > 当日日线 preClose > 严格前日 close
            prev_close = 0.0
            prev_src = ""
            try:
                prev_close = float(tick_meta.get("pre_close") or 0)
                if prev_close > 0:
                    prev_src = "tick_lastClose"
            except Exception:
                prev_close = 0.0
            if prev_close <= 0:
                try:
                    prev_close = float(preclose_by.get(ymd) or 0)
                    if prev_close > 0:
                        prev_src = "daily_preClose"
                except Exception:
                    prev_close = 0.0
            if prev_close <= 0:
                prev_candidates = [y for y in (yords or []) if y and y < ymd]
                if prev_candidates:
                    prev_ymd = max(prev_candidates)
                    try:
                        prev_close = float(close_by.get(prev_ymd) or 0)
                        if prev_close > 0:
                            prev_src = "daily_close[%s]" % prev_ymd
                    except Exception:
                        prev_close = 0.0
            if prev_close <= 0:
                _log("SKIP_NO_PREV_CLOSE|%s|%s" % (c6, ymd))
                continue

            limit_up = 0.0
            limit_down = 0.0
            try:
                limit_up = float(tick_meta.get("limit_up") or 0)
            except Exception:
                limit_up = 0.0
            try:
                limit_down = float(tick_meta.get("limit_down") or 0)
            except Exception:
                limit_down = 0.0
            if limit_up <= 0:
                limit_up = round(prev_close * (1.0 + lr), prec)
            if limit_down <= 0:
                limit_down = round(prev_close * (1.0 - lr), prec)
            # 仅异常昨收来源打日志，避免 FormulaOutput 刷屏拖垮 QMT
            if prev_src and prev_src != "tick_lastClose":
                _log(
                    "PREV|%s|%s|src=%s|prev=%.4f|lu=%.4f"
                    % (c6, ymd, prev_src, prev_close, limit_up)
                )

            day_intents = []

            if is_surge:
                # ---- 盘中买入冲高卖出：昨收+4/6/8% 各三分之一单点 + 末日定时清仓（无T点清仓分支） ----
                leg_specs = [
                    ("%s:SURGE4" % c6, NAME_SURGE4, surge_g4),
                    ("%s:SURGE6" % c6, NAME_SURGE6, surge_g6),
                    ("%s:SURGE8" % c6, NAME_SURGE8, surge_g8),
                ]
                v4, v6, v8 = _thirds_absorb_dust(base)
                base_vols = [v4, v6, v8]
                pending_idx = []
                trigs = [0.0, 0.0, 0.0]
                for i, (lk, _nm, thr) in enumerate(leg_specs):
                    if lk in filled_legs:
                        continue
                    if thr <= 0 or prev_close <= 0:
                        continue
                    trig = _clamp_px(
                        round(prev_close * (1.0 + float(thr)), prec),
                        limit_down,
                        limit_up if limit_up > 0 else prev_close * (1.0 + float(thr)),
                    )
                    if trig <= 0:
                        continue
                    trigs[i] = float(trig)
                    pending_idx.append(i)
                sell_vols = [0, 0, 0]
                if pending_idx:
                    if len(pending_idx) == 3 and (v4 + v6 + v8) >= 100:
                        sell_vols = [v4, v6, v8]
                        pair_cap = hold
                        if sum(sell_vols) > pair_cap:
                            rem = pair_cap
                            for i in (0, 1, 2):
                                sell_vols[i] = min(sell_vols[i], rem)
                                sell_vols[i] = _full_lot(sell_vols[i])
                                rem = max(0, rem - sell_vols[i])
                    elif len(pending_idx) == 1:
                        i = pending_idx[0]
                        sell_vols[i] = _fold_dust(hold, hold, trigs[i], min_amt)
                    else:
                        rem_hold = hold
                        allocated = 0
                        for j, i in enumerate(pending_idx):
                            t = base_vols[i] if base_vols[i] >= 100 else _full_lot(base // 3)
                            if j == len(pending_idx) - 1:
                                vol = _full_lot(rem_hold - allocated)
                            else:
                                vol = min(_full_lot(t), _full_lot(rem_hold - allocated))
                                if vol < 100:
                                    vol = 0
                            sell_vols[i] = vol
                            allocated += vol
                        last_i = pending_idx[-1]
                        if sell_vols[last_i] >= 100:
                            used = sum(sell_vols[i] for i in pending_idx if i != last_i)
                            sell_vols[last_i] = _fold_dust(
                                max(sell_vols[last_i], hold - used),
                                hold - used,
                                trigs[last_i],
                                min_amt,
                            )
                    for i in pending_idx:
                        vol = int(sell_vols[i] or 0)
                        vol = min(vol, avail, hold)
                        vol = _full_lot(vol)
                        if vol < 100 or trigs[i] <= 0:
                            continue
                        if len(pending_idx) == 1 or i == pending_idx[-1]:
                            others = sum(sell_vols[j] for j in pending_idx if j != i)
                            cap = _full_lot(max(0, hold - others))
                            vol = _fold_dust(vol, cap if cap >= 100 else hold, trigs[i], min_amt)
                            vol = _full_lot(min(vol, avail, hold))
                        if vol < 100:
                            continue
                        lk, nm, _thr = leg_specs[i]
                        day_intents.append({
                            "kind": "single_sell",
                            "name": nm,
                            "leg": lk,
                            "trigger": float(trigs[i]),
                            "drop": 0.0,
                            "volume": int(vol),
                        })

            elif is_ind:
                # ---- 盘中买入次日了结：生成时刻最新价-1tick 弹性全仓 + T点清仓分支 + 末日定时清仓 ----
                # 对齐实盘：触价=策略生成时间的最新价-1跳；运行开始时间只限制何时可成交
                leg_open_pull = "%s:OPEN_PULL:%s" % (c6, ymd)
                cur_px = _px_at_generation(ticks, gen_sec)
                if cur_px <= 0:
                    try:
                        cur_px = float(open_by.get(ymd) or 0)
                    except Exception:
                        cur_px = 0.0
                if leg_open_pull not in filled_legs and cur_px > 0 and avail >= 100:
                    trig = round(float(cur_px) - float(tick_sz), prec)
                    if trig > 0:
                        trig = _clamp_px(trig, limit_down, limit_up if limit_up > 0 else trig)
                    if trig > 0:
                        day_intents.append({
                            "kind": "best_sell",
                            "name": NAME_OPEN_PULL,
                            "leg": leg_open_pull,
                            "trigger": float(trig),
                            "drop": float(open_pull_drop),
                            "volume": int(avail),
                        })

                # T 点封板分支 → 决定清仓腿形态（仅 T 后可成交）
                _bres, sealed, after_sec = _resolve_clear_branch(ticks, branch_sec, limit_up)
                if avail >= 100 and prev_close > 0:
                    if sealed:
                        day_intents.append({
                            "kind": "breakthrough_sell",
                            "name": NAME_CLEAR_LU + "·开板突破卖",
                            "leg": "%s:CLEAR_BRK:%s" % (c6, ymd),
                            "trigger": float(limit_up),
                            "drop": 0.0,
                            "volume": int(avail),
                            "is_clear": True,
                            "after_sec": after_sec,
                        })
                    elif _is_growth_board(c6):
                        if thr_clear_growth > 0:
                            ct = _clamp_px(
                                round(prev_close * (1.0 + thr_clear_growth), prec),
                                limit_down, limit_up,
                            )
                            if ct > 0:
                                day_intents.append({
                                    "kind": "best_sell",
                                    "name": NAME_CLEAR_GROWTH,
                                    "leg": "%s:CLEAR_G:%s" % (c6, ymd),
                                    "trigger": float(ct),
                                    "drop": clear_drop_growth,
                                    "volume": int(avail),
                                    "is_clear": True,
                                    "after_sec": after_sec,
                                })
                    elif thr_clear_main > 0:
                        ct = _clamp_px(
                            round(prev_close * (1.0 + thr_clear_main), prec),
                            limit_down, limit_up,
                        )
                        if ct > 0:
                            lu_imm = (
                                abs(float(thr_clear_main) - 0.10) < 1e-9
                                and float(clear_drop_main) <= 1e-12
                            )
                            if lu_imm:
                                day_intents.append({
                                    "kind": "single_sell",
                                    "name": NAME_CLEAR_LU,
                                    "leg": "%s:CLEAR_LU:%s" % (c6, ymd),
                                    "trigger": float(ct),
                                    "drop": 0.0,
                                    "volume": int(avail),
                                    "is_clear": True,
                                    "after_sec": after_sec,
                                })
                            else:
                                day_intents.append({
                                    "kind": "best_sell",
                                    "name": NAME_CLEAR_MAIN,
                                    "leg": "%s:CLEAR_M:%s" % (c6, ymd),
                                    "trigger": float(ct),
                                    "drop": clear_drop_main,
                                    "volume": int(avail),
                                    "is_clear": True,
                                    "after_sec": after_sec,
                                })
            else:
                # ---- 马总逻辑1卖：开盘涨幅半仓 + 近10日涨停半仓 + 清仓（无 T 点细模拟，保持旧口径） ----
                thr = open_gain_growth if _is_growth_board(c6) else open_gain_main
                open_ref = _clamp_px(round(prev_close * (1.0 + thr), 2), limit_down, limit_up) if prev_close > 0 else 0.0
                _sel_raw = sel_map.get(c6) or sel_map.get(_full_from_c6(c6))
                sel_d = _parse_d(_sel_raw) if _sel_raw is not None else None
                lu_raw = _recent_lu_trigger_from_daily(
                    c6, close_by, high_by, yords, day_d, sel_d, offset=lu_high_offset
                )
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
                clear_px = round(float(limit_up) - float(tick_sz), prec) if limit_up > 0 else 0.0
                force = clear_px <= 0
                nm = "%s-第%d日%s" % (sc_prefix, ui_n, "无条件清仓" if force else "涨停外清仓")
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
                after_sec = intent.get("after_sec")
                if kind == "best_sell":
                    # 清仓腿带 after_sec：T 点前不撮合
                    if after_sec is not None:
                        # 复用 breakthrough 的 after 过滤：临时截断 ticks
                        ticks_use = [t for t in ticks if t[0] >= int(after_sec)]
                        fill = _replay_best_sell(
                            ticks_use, float(intent["trigger"]), float(intent["drop"]),
                            run_start_sec, run_end_sec,
                        )
                    else:
                        fill = _replay_best_sell(
                            ticks, float(intent["trigger"]), float(intent["drop"]),
                            run_start_sec, run_end_sec,
                        )
                elif kind == "single_sell":
                    if after_sec is not None:
                        ticks_use = [t for t in ticks if t[0] >= int(after_sec)]
                        fill = _replay_single_sell(
                            ticks_use, float(intent["trigger"]), run_start_sec, run_end_sec,
                        )
                    else:
                        fill = _replay_single_sell(
                            ticks, float(intent["trigger"]), run_start_sec, run_end_sec,
                        )
                elif kind == "breakthrough_sell":
                    fill = _replay_breakthrough_sell(
                        ticks, float(intent["trigger"]), run_start_sec, run_end_sec,
                        after_sec=after_sec,
                    )
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
                taken = _consume_available_lots(st, vol, day_d, from_t1)
                if taken < 100:
                    continue
                vol = taken
                amount = round(float(px) * vol, 2)
                # baseline 不降（与实盘注释一致）
                if int(st.get("volume") or 0) < 100:
                    st["volume"] = 0
                    st["baseline"] = 0
                    st["lots"] = []
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
                    "variant": variant,
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
    if run_tag:
        _DONE_RUN_TAGS.add(str(run_tag))


def run_backtest(ContextInfo):
    """Entry for hot-reload probe (探针); also used by init for one-shot paste."""
    tag = str(CFG.get("run_tag") or "")
    if tag and tag in _DONE_RUN_TAGS:
        _log("SKIP_ALREADY_DONE|%s" % tag)
        return
    try:
        _run_backtest(ContextInfo)
    except Exception as e:
        _emit("ERROR", {"msg": str(e)[:500], "trace": traceback.format_exc()[:1500]})


def init(ContextInfo):
    run_backtest(ContextInfo)


def handlebar(ContextInfo):
    pass
