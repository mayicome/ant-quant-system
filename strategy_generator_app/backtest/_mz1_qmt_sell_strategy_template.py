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
import os
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


def _datadir_roots():
    return (
        r"D:\国金证券QMT交易端2\datadir",
        r"D:\国金证券QMT交易端\datadir",
    )


def _market_of(full_code):
    s = str(full_code or "").strip().upper()
    if "." in s:
        suf = s.split(".", 1)[1]
        if suf in ("SH", "SZ", "BJ"):
            return suf
    c6 = _code6(s)
    if c6.startswith("6"):
        return "SH"
    if c6.startswith(("4", "8")):
        return "BJ"
    return "SZ"


def _daily_ymds_from_dat(full_code):
    """YYYYMMDD from datadir/{SH|SZ|BJ}/86400/{code}.DAT.

    QMT daily file: 8-byte header, then 64-byte bars. Byte 0 of each bar is a
    local Unix timestamp (uint32). None if the file cannot be read.
    """
    try:
        c6 = _code6(full_code)
        if not c6:
            return None
        mkt = _market_of(full_code)
        saw = False
        for root in _datadir_roots():
            path = os.path.join(root, mkt, "86400", c6 + ".DAT")
            if not os.path.isfile(path):
                alt = os.path.join(root, mkt, "86400", c6 + ".dat")
                if os.path.isfile(alt):
                    path = alt
                else:
                    continue
            saw = True
            try:
                import struct
                fh = open(path, "rb")
                try:
                    data = fh.read()
                finally:
                    fh.close()
            except OSError:
                return None
            if len(data) < 72:
                return []
            n = (len(data) - 8) // 64
            out = []
            seen_d = set()
            for i in range(n):
                v = struct.unpack_from("<I", data, 8 + i * 64)[0]
                if v < 631152000 or v > 4102444800:
                    continue
                try:
                    ymd = datetime.fromtimestamp(v).strftime("%Y%m%d")
                except Exception:
                    continue
                if ymd not in seen_d:
                    seen_d.add(ymd)
                    out.append(ymd)
            out.sort()
            return out
        if not saw:
            return None
        return []
    except Exception:
        return None


def _api_daily_ymds(ContextInfo, full_code, start_s, end_s):
    """Daily dates from get_market_data_ex. None means the read failed."""
    try:
        daily, err = _get_market(ContextInfo, full_code, "1d", start_s, end_s)
        if err:
            return None
        ymds = []
        seen = set()
        seq = []
        # Index is often a real datetime. The time column is often epoch ms;
        # reading digits[:8] of that int is not a calendar day.
        if hasattr(daily, "index"):
            try:
                seq = list(daily.index)
            except Exception:
                seq = []
        parsed = _as_ymd_list(seq) if seq else []
        if not any(parsed):
            parsed = _as_ymd_list(_series(daily, "time", "stamp", "stime"))
        for y in parsed:
            if y and y not in seen:
                seen.add(y)
                ymds.append(y)
        ymds.sort()
        return ymds
    except Exception:
        return None


def _local_daily_ymds(ContextInfo, full_code, start_s, end_s):
    """Dates used by the skip check. A covering 86400 DAT wins over the API."""
    disk = _daily_ymds_from_dat(full_code)
    if disk and _daily_range_covered(disk, start_s, end_s):
        return disk
    api = _api_daily_ymds(ContextInfo, full_code, start_s, end_s)
    if api and _daily_range_covered(api, start_s, end_s):
        return api
    if api:
        return api
    if disk is not None:
        return disk
    return api


def _daily_range_covered(ymds, daily_start, end_s):
    """True only when local daily bars reach the requested end and the lookback."""
    if not ymds:
        return False
    if str(ymds[-1]) < str(end_s):
        return False
    first = str(ymds[0])
    ask = str(daily_start)
    if first <= ask:
        return True
    try:
        d0 = date(int(first[0:4]), int(first[4:6]), int(first[6:8]))
        d_ask = date(int(ask[0:4]), int(ask[4:6]), int(ask[6:8]))
        gap = (d0 - d_ask).days
        if 0 <= gap <= 10:
            return True
    except Exception:
        return False
    return False


def _local_tick_ymds(full_code):
    """YYYYMMDD set of non-empty tick files. None if datadir cannot be listed."""
    try:
        c6 = _code6(full_code)
        if not c6:
            return None
        mkt = _market_of(full_code)
        saw_root = False
        for root in _datadir_roots():
            base = os.path.join(root, mkt, "0")
            if not os.path.isdir(base):
                continue
            saw_root = True
            folder = os.path.join(base, c6)
            try:
                names = os.listdir(folder)
            except OSError:
                return set()
            out = set()
            for fn in names:
                if len(fn) < 12 or not fn[:8].isdigit():
                    continue
                if not fn.lower().endswith(".dat"):
                    continue
                try:
                    sz = os.path.getsize(os.path.join(folder, fn))
                except OSError:
                    return None
                if sz > 0:
                    out.add(fn[:8])
            return out
        if not saw_root:
            return None
        return set()
    except Exception:
        return None


# mz1_qmt_fine.DEFAULT_TICK_DEPTH_CALENDAR_DAYS：自然日，不是交易日。
DEFAULT_TICK_DEPTH_CALENDAR_DAYS = 29


def _tick_depth_floor_ymd():
    """Earliest day download_history_data(tick) can still fetch. date.today() inside QMT."""
    try:
        floor = date.today() - timedelta(days=DEFAULT_TICK_DEPTH_CALENDAR_DAYS)
        return floor.strftime("%Y%m%d")
    except Exception:
        return ""


def _tick_download_plan(tick_days, files, depth_ymd, req_start, req_end):
    """Decide whether a tick download can add anything.

    Returns ('skip', ''), ('skip_old', '') or ('download', start_ymd).
    Days older than the broker depth are never requested. A download start
    is clamped up to that floor; the end stays req_end.
    """
    missing_old = []
    missing_in = []
    known = files is not None
    for ymd in tick_days or []:
        if known and ymd in files:
            continue
        if depth_ymd and ymd < depth_ymd:
            missing_old.append(ymd)
        else:
            missing_in.append(ymd)
    if missing_in:
        dl_start = req_start or depth_ymd or ""
        if depth_ymd and (not dl_start or dl_start < depth_ymd):
            dl_start = depth_ymd
        if req_end and dl_start and dl_start > req_end:
            return "skip_old", ""
        return "download", dl_start
    if missing_old:
        return "skip_old", ""
    return "skip", ""


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
    """Calendar day. Epoch ms/ns must not be sliced to the first 8 digits."""
    if tm is None:
        return None
    if isinstance(tm, datetime):
        return tm.strftime("%Y%m%d")
    if isinstance(tm, date):
        return tm.strftime("%Y%m%d")
    if type(tm).__name__ == "datetime64":
        try:
            import pandas as pd
            return pd.Timestamp(tm).strftime("%Y%m%d")
        except Exception:
            pass
    v = None
    try:
        raw = tm.item() if hasattr(tm, "item") else tm
        v = float(raw)
    except Exception:
        v = None
    s = str(tm).strip()
    if not s and v is None:
        return None
    if v is None and len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[0:4] + s[5:7] + s[8:10]
    if v is None:
        try:
            v = float(s)
        except Exception:
            v = None
    if v is not None and v == v:
        try:
            if v >= 1e17:
                return datetime.fromtimestamp(v / 1e9).strftime("%Y%m%d")
            if v >= 1e14:
                return datetime.fromtimestamp(v / 1e6).strftime("%Y%m%d")
            if v >= 1e13:
                # YYYYMMDDHHMMSS, not epoch milliseconds
                return str(int(v))[:8]
            if v >= 1e12:
                return datetime.fromtimestamp(v / 1e3).strftime("%Y%m%d")
            if v >= 1e9:
                return datetime.fromtimestamp(v).strftime("%Y%m%d")
            if v >= 1e7:
                y = str(int(v))[:8]
                if y.startswith("19") or y.startswith("20"):
                    return y
        except Exception:
            pass
    digits = "".join(ch for ch in s if ch.isdigit())
    if len(digits) >= 17:
        try:
            return datetime.fromtimestamp(float(digits) / 1e9).strftime("%Y%m%d")
        except Exception:
            return None
    if len(digits) >= 14:
        return digits[:8]
    if len(digits) >= 13:
        try:
            return datetime.fromtimestamp(float(digits) / 1e3).strftime("%Y%m%d")
        except Exception:
            return None
    if len(digits) >= 8:
        y = digits[:8]
        if y.startswith("19") or y.startswith("20"):
            return y
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


def _mz1_leg_has_clear_branch(is_growth, thr_main, drop_main, thr_growth, drop_growth):
    """马总卖 run()：只有这两条清仓腿才写 clear_branch_at。

    主板 CLEAR_GAIN_MAIN=0.10 且回落 0 → 涨停即清仓。
    非主板 CLEAR_GAIN_GROWTH=0.20 且回落 0。
    创业板/科创默认 13% + 回落 1.5% 不带 T 点，封住也不改开板突破卖。
    """
    if is_growth:
        return abs(float(thr_growth) - 0.20) < 1e-9 and float(drop_growth) <= 1e-12
    return abs(float(thr_main) - 0.10) < 1e-9 and float(drop_main) <= 1e-12


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


def _tick_row(t):
    ask = 0
    try:
        if len(t) > 5:
            ask = int(float(t[5] or 0))
    except Exception:
        ask = 0
    return t[0], t[1], t[2], t[3], t[4], ask


# core.rule_activation.PRICE_TOUCH_EPS / is_limit_up_sealed
_SEAL_EPS = 0.011


def _is_limit_up_sealed(last_price, limit_up, ask_vol=0):
    """现价贴涨停且卖一量为 0。与 core.rule_activation.is_limit_up_sealed 一致。"""
    try:
        lp = float(last_price or 0)
        lu = float(limit_up or 0)
    except Exception:
        return False
    if lu <= 0 or lp <= 0:
        return False
    if lp < lu - _SEAL_EPS:
        return False
    try:
        av = int(float(ask_vol or 0))
    except Exception:
        av = 0
    return av <= 0


def _replay_breakthrough_sell(ticks, price, run_start_sec, run_end_sec, after_sec=None, prec=2):
    """向下突破：现价跌破触发价则按这一笔现价卖。开板不走这里。"""
    if price <= 0 or not ticks:
        return None
    try:
        prec = int(prec)
    except Exception:
        prec = 2
    if prec < 0:
        prec = 2
    px = round(float(price), prec)
    for sec, tm, last, _hi, _lo, _ask in (_tick_row(t) for t in ticks):
        if after_sec is not None and sec < after_sec:
            continue
        if run_start_sec is not None and sec < run_start_sec:
            continue
        if run_end_sec is not None and sec > run_end_sec:
            continue
        if last <= 0:
            continue
        if round(float(last), prec) < px:
            return (sec, tm, last)
    return None


def _replay_open_board_sell(ticks, limit_up, run_start_sec, run_end_sec, after_sec=None):
    """开板突破卖：实盘只看现价 < 涨停价，不看最低价。"""
    if limit_up <= 0 or not ticks:
        return None
    for sec, tm, last, hi, lo, _ask in (_tick_row(t) for t in ticks):
        if after_sec is not None and sec < after_sec:
            continue
        if run_start_sec is not None and sec < run_start_sec:
            continue
        if run_end_sec is not None and sec > run_end_sec:
            continue
        if last + 1e-9 < float(limit_up):
            return (sec, tm, last)
    return None


def _resolve_clear_branch(ticks, branch_sec, limit_up):
    """core.clear_branch.process_clear_branch_tick：T 后第一笔有效现价快照。

    返回 (resolved, sealed, after_sec)。没有 T 后 tick 则未决议，清仓腿不能成交。
    """
    if limit_up <= 0:
        return False, False, branch_sec
    for sec, tm, last, hi, lo, ask in (_tick_row(t) for t in ticks or []):
        if sec < branch_sec:
            continue
        if last <= 0:
            continue
        sealed = _is_limit_up_sealed(last, limit_up, ask)
        return True, sealed, sec
    return False, False, branch_sec


def _half_remaining_volume(avail, price, min_order_amount):
    """qmt_builtin.keep_half_utils.half_remaining_volume。"""
    avail = _full_lot(avail)
    if avail < 100:
        return 0, "no_avail"
    half = _half_lot(avail)
    remain = avail - half
    if remain < 100:
        return avail, "absorb_lot"
    if min_order_amount > 0 and price > 0 and remain * float(price) < float(min_order_amount):
        return avail, "absorb_min_amount"
    if half < 100:
        return avail, "absorb_lot"
    return half, "half"


def _classify_strength(last_price, cost_price, period_high, profit_keep_pct, trail_pullback_pct):
    """qmt_builtin.keep_half_utils.classify_strength。成本缺失视为弱势。"""
    try:
        lp = float(last_price or 0)
    except Exception:
        lp = 0.0
    try:
        cost = float(cost_price or 0)
    except Exception:
        cost = 0.0
    try:
        hp = float(period_high or 0)
    except Exception:
        hp = 0.0
    if hp <= 0:
        hp = lp
    profit_pct = ((lp - cost) / cost * 100.0) if (cost > 0 and lp > 0) else 0.0
    pullback_pct = ((hp - lp) / hp * 100.0) if (hp > 0 and lp > 0) else 0.0
    if cost <= 0 or lp <= 0:
        strong = False
        reason = "no_cost_or_price"
    else:
        strong = (profit_pct + 1e-12 >= float(profit_keep_pct)) and (
            pullback_pct <= float(trail_pullback_pct) + 1e-12
        )
        reason = "strong" if strong else "weak"
    return {
        "strong": bool(strong),
        "reason": reason,
        "clear_volume_mode": "half_remaining" if strong else "remaining",
        "profit_pct": profit_pct,
        "pullback_pct": pullback_pct,
        "period_high": hp,
    }


def _first_scheduled_tick(ticks, clear_sec, window_sec=300):
    """到点后 300 秒内的第一笔。超时则实盘记 scheduled_clear_skip，不成交。"""
    if clear_sec is None:
        return None
    for t in ticks or []:
        sec = t[0]
        if sec < clear_sec:
            continue
        if sec > clear_sec + int(window_sec):
            return None
        return t
    return None

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
    asks = _series(payload, "askVol", "askVolume")
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
        ask = 0
        try:
            if asks and i < len(asks):
                raw_ask = asks[i]
                if isinstance(raw_ask, (list, tuple)):
                    raw_ask = raw_ask[0] if raw_ask else 0
                ask = int(float(raw_ask or 0))
        except Exception:
            ask = 0
        out.append((sec, times[i], last, hi, lo, ask))
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
    for sec, tm, last, hi, lo, _ask in (_tick_row(t) for t in ticks):
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
    """引擎序号（含买入日）：买入日=1。

    接续卖出仿真窗口常从「买入次日」起，trading_days 不含买入日。
    若只在窗口内计数，持有 N 日会对不齐（例：买 11-06、窗 11-09～10、
    hold_n=3 时窗口内最多数到 2，末日清仓永不触发）。
    因此以锚定买入日～as_of 的交易日跨度计数；trading_days 仅作节假日微调。
    """
    anchor = st.get("last_buy_date") or st.get("entry_date")
    if isinstance(anchor, str):
        anchor = _parse_d(anchor)
    if anchor is None:
        return 0
    if day_d < anchor:
        return 0
    span = _weekday_days(anchor, day_d)
    known = [d for d in (trading_days or []) if anchor <= d <= day_d]
    if known:
        days = sorted(set(span) | set(known))
    else:
        days = span
    return len(days)


_ELASTIC_CFG = None


def _elastic_cfg():
    """与实盘 data/config.ini [Elastic] 一致。QMT 读不到文件时用同一组默认值。"""
    global _ELASTIC_CFG
    if _ELASTIC_CFG is not None:
        return _ELASTIC_CFG
    cfg = {
        "confirm_ticks": 4,
        "cooldown": 2,
        "dynamic": 2,
        "room_tight": 1.0,
        "drop_tight": 0.5,
        "limit_minus": 1.0,
        "drop_scale": 0.35,
        "max_drop": 3.5,
    }
    try:
        import configparser
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "data", "config.ini",
        )
        if not os.path.isfile(path):
            path = r"D:\蚂蚁量化系统\data\config.ini"
        cp = configparser.ConfigParser()
        cp.read(path, encoding="utf-8-sig")
        if cp.has_section("Elastic"):
            def _i(key, default):
                try:
                    return int(float(cp.get("Elastic", key)))
                except Exception:
                    return default

            def _f(key, default):
                try:
                    return float(cp.get("Elastic", key))
                except Exception:
                    return default

            cfg["confirm_ticks"] = _i("confirm_ticks", cfg["confirm_ticks"])
            cfg["cooldown"] = _i("cooldown_after_extreme_ticks", cfg["cooldown"])
            cfg["dynamic"] = _i("dynamic_thresholds", cfg["dynamic"])
            cfg["room_tight"] = _f("room_tight_pp", cfg["room_tight"])
            cfg["drop_tight"] = _f("drop_tight", cfg["drop_tight"])
            cfg["limit_minus"] = _f("limit_minus_pp", cfg["limit_minus"])
            cfg["drop_scale"] = _f("drop_scale", cfg["drop_scale"])
            cfg["max_drop"] = _f("max_drop_percent", cfg["max_drop"])
    except Exception:
        pass
    if cfg["confirm_ticks"] < 0:
        cfg["confirm_ticks"] = 4
    if cfg["confirm_ticks"] == 0:
        cfg["confirm_ticks"] = 1
    if cfg["cooldown"] < 0:
        cfg["cooldown"] = 0
    _ELASTIC_CFG = cfg
    return cfg


def _room_blend_start(drop_pct):
    """ant_elastic_sell_lite.resolve_room_blend_start：回落 <=2.5% 时从距涨停 3 个百分点开始收紧。"""
    try:
        drop = float(drop_pct)
    except Exception:
        drop = 2.5
    if drop <= 2.5:
        return 3.0
    if drop >= 5.0:
        return 1.5
    w = (drop - 2.5) / (5.0 - 2.5)
    return 3.0 + w * (1.5 - 3.0)


def _elastic_target(highest, trigger, drop_pct, limit_up, pre_close):
    """compute_best_sell_fallback：靠近涨停时回落收到 drop_tight，否则用名义回落。"""
    try:
        highest = float(highest or 0)
    except Exception:
        highest = 0.0
    if highest <= 0:
        return 0.0
    cfg = _elastic_cfg()
    try:
        drop_nom = float(drop_pct or 0)
    except Exception:
        drop_nom = 0.0
    dyn = int(cfg["dynamic"])
    if dyn <= 0:
        return highest * (1.0 - drop_nom / 100.0)
    if dyn == 1:
        try:
            trig = float(trigger or 0)
            profit_pct = max(0.0, (highest / trig - 1.0) * 100.0) if trig > 0 else 0.0
        except Exception:
            profit_pct = 0.0
        eff = min(float(cfg["max_drop"]), drop_nom + profit_pct * float(cfg["drop_scale"]))
        return highest * (1.0 - eff / 100.0)
    try:
        lu = float(limit_up or 0)
        pc = float(pre_close or 0)
    except Exception:
        lu, pc = 0.0, 0.0
    if lu <= 0 or pc <= 0:
        return highest * (1.0 - drop_nom / 100.0)
    room_pp = (lu - highest) / pc * 100.0
    room_tight = float(cfg["room_tight"])
    drop_tight = float(cfg["drop_tight"])
    blend_start = _room_blend_start(drop_nom)
    if blend_start <= room_tight + 1e-9:
        blend_start = room_tight + 0.5
    if room_pp >= blend_start - 1e-9:
        eff = drop_nom
    elif room_pp <= room_tight + 1e-9:
        eff = drop_tight
    else:
        w = (room_pp - room_tight) / (blend_start - room_tight)
        eff = drop_tight + w * (drop_nom - drop_tight)
    fb = highest * (1.0 - eff / 100.0)
    if room_pp <= room_tight + 1e-9:
        floor_px = highest - float(cfg["limit_minus"]) * pc / 100.0
        fb = max(fb, floor_px)
    return fb


def _replay_best_sell(ticks, trigger, drop_pct, run_start_sec, run_end_sec, seed_high=0.0, limit_up=0.0, pre_close=0.0):
    """弹性卖出，对齐实盘 ant_tick_runner：只看现价。

    现价上破触发价才开始跟踪。新高后冷却 cooldown 笔，之后要连续 confirm 笔
    现价 <= 目标价且低于最高价才卖。不用分笔里的当日最高价、最低价。
    seed_high：已有持有期最高价。进入区间的第一笔只完成跟踪，不成交。
    """
    if trigger <= 0 or not ticks:
        return None
    try:
        pre_seed = float(seed_high or 0)
    except Exception:
        pre_seed = 0.0
    cfg = _elastic_cfg()
    confirm_ticks = int(cfg["confirm_ticks"])
    cooldown_ticks = int(cfg["cooldown"])
    triggered = False
    highest = None
    highest_idx = 0
    pull_cnt = 0
    tick_idx = 0
    for sec, tm, last, _hi, _lo, _ask in (_tick_row(t) for t in ticks):
        if run_start_sec is not None and sec < run_start_sec:
            continue
        if run_end_sec is not None and sec > run_end_sec:
            continue
        if last <= 0:
            continue
        tick_idx += 1
        if pre_seed > 0 and not triggered:
            triggered = True
            highest = last if last > pre_seed else pre_seed
            highest_idx = tick_idx
            pull_cnt = 0
            continue
        if last > trigger:
            if not triggered:
                triggered = True
                highest = last if pre_seed <= last else pre_seed
                highest_idx = tick_idx
                pull_cnt = 0
                continue
            if highest is None or last > highest:
                highest = last
                highest_idx = tick_idx
                pull_cnt = 0
                continue
        if triggered and highest is not None and highest > 0:
            if highest_idx > 0 and (tick_idx - highest_idx) <= cooldown_ticks:
                continue
            target = _elastic_target(highest, trigger, drop_pct, limit_up, pre_close)
            if last <= target and last < float(highest):
                pull_cnt += 1
                if pull_cnt < confirm_ticks:
                    continue
                return (sec, tm, last)
            pull_cnt = 0
    return None


def _eval_no_new_high(period_high, period_high_prev, streak, need_days=3):
    """keep_half_utils.eval_no_new_high。"""
    try:
        hp = float(period_high or 0)
    except Exception:
        hp = 0.0
    try:
        prev = float(period_high_prev or 0)
    except Exception:
        prev = 0.0
    try:
        streak = int(streak or 0)
    except Exception:
        streak = 0
    if streak < 0:
        streak = 0
    try:
        need = int(need_days or 3)
    except Exception:
        need = 3
    if need < 1:
        need = 1
    if prev <= 0 or hp <= 0:
        return streak, False
    if hp > prev + 1e-12:
        return 0, False
    streak = streak + 1
    return streak, streak >= need


def _replay_single_sell(ticks, price, run_start_sec, run_end_sec):
    """单点卖出，对齐实盘 ant_tick_runner：只看现价 >= 触发价。

    分笔里的 high 是当日最高价。09:50 现价已经离开涨停时，不能用这个最高价
    当成触价、再按更低的现价成交。
    """
    if price <= 0 or not ticks:
        return None
    for sec, tm, last, _hi, _lo, _ask in (_tick_row(t) for t in ticks):
        if run_start_sec is not None and sec < run_start_sec:
            continue
        if run_end_sec is not None and sec > run_end_sec:
            continue
        if last + 1e-9 >= price:
            return (sec, tm, last)
    return None


def _replay_scheduled_clear(ticks, clear_px, clear_sec, force, run_end_sec):
    """定时清仓：到点后 300 秒内的第一笔现价。低于触发价才卖，否则当天这腿作废。"""
    if not ticks or clear_sec is None:
        return None
    window_end = int(clear_sec) + 300
    for sec, tm, last, _hi, _lo, _ask in (_tick_row(t) for t in ticks):
        if sec < clear_sec:
            continue
        if run_end_sec is not None and sec > run_end_sec:
            break
        if sec > window_end:
            return None
        if last <= 0:
            continue
        if force:
            return (sec, tm, last)
        if clear_px > 0 and last + 1e-9 < clear_px:
            return (sec, tm, last)
        return None
    return None


def _quote_intent_fill(intent, ticks, run_start_sec, run_end_sec, prec, clear_sec, limit_up, prev_close):
    """这条规则若单独看整天，会在哪一笔成交。不扣仓。"""
    kind = intent.get("kind")
    after_sec = intent.get("after_sec")
    if kind == "best_sell":
        _seed = float(intent.get("seed_high") or 0)
        if after_sec is not None:
            ticks_use = [t for t in ticks if t[0] >= int(after_sec)]
            return _replay_best_sell(
                ticks_use, float(intent["trigger"]), float(intent["drop"]),
                run_start_sec, run_end_sec, seed_high=_seed,
                limit_up=limit_up, pre_close=prev_close,
            )
        return _replay_best_sell(
            ticks, float(intent["trigger"]), float(intent["drop"]),
            run_start_sec, run_end_sec, seed_high=_seed,
            limit_up=limit_up, pre_close=prev_close,
        )
    if kind == "single_sell":
        if after_sec is not None:
            ticks_use = [t for t in ticks if t[0] > int(after_sec)]
            return _replay_single_sell(
                ticks_use, float(intent["trigger"]), run_start_sec, run_end_sec,
            )
        return _replay_single_sell(
            ticks, float(intent["trigger"]), run_start_sec, run_end_sec,
        )
    if kind == "breakthrough_sell":
        if intent.get("seal_open"):
            return _replay_open_board_sell(
                ticks, float(intent["trigger"]), run_start_sec, run_end_sec,
                after_sec=after_sec,
            )
        return _replay_breakthrough_sell(
            ticks, float(intent["trigger"]), run_start_sec, run_end_sec,
            after_sec=after_sec, prec=prec,
        )
    if kind == "scheduled_clear":
        preset = intent.get("preset_fill")
        if preset:
            return preset
        return _replay_scheduled_clear(
            ticks, float(intent.get("trigger") or 0), clear_sec,
            bool(intent.get("force")), run_end_sec,
        )
    return None


def _skip_lu_ex_div(yords, close_by, open_by, ymd, quote_prev, limit_ratio, day_open=0.0):
    """除权日不挂近涨停腿。日线是不复权，昨收或今开相对缓存昨收跳空才跳过。"""
    prevs = [y for y in (yords or []) if y and y < ymd]
    if not prevs:
        return False
    try:
        cache_close = float((close_by or {}).get(max(prevs)) or 0)
    except Exception:
        cache_close = 0.0
    if cache_close <= 0:
        return False
    try:
        quote_prev = float(quote_prev or 0)
    except Exception:
        quote_prev = 0.0
    if quote_prev > 0 and abs(quote_prev / cache_close - 1.0) + 1e-12 >= 0.08:
        return True
    o = 0.0
    try:
        o = float(day_open or 0)
    except Exception:
        o = 0.0
    if o <= 0:
        try:
            o = float((open_by or {}).get(ymd) or 0)
        except Exception:
            o = 0.0
    if o <= 0:
        return False
    gap = (o / cache_close) - 1.0
    try:
        thr = -(float(limit_ratio) + 0.005)
    except Exception:
        thr = -0.105
    return gap < thr


def iter_backtest(ContextInfo):
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
    tick_lo = start_d.strftime("%Y%m%d")
    tick_depth_ymd = _tick_depth_floor_ymd()
    n_dl = len(codes)
    n_skip_1d = 0
    n_dl_1d = 0
    n_skip_tick = 0
    n_skip_tick_old = 0
    n_dl_tick = 0
    for i_dl, c6 in enumerate(codes):
        yield
        fc = _full_from_c6(c6)
        ymds = _local_daily_ymds(ContextInfo, fc, dl_start, dl_end)
        if ymds is None or not _daily_range_covered(ymds, dl_start, dl_end):
            e1 = _call_download(ContextInfo, fc, "1d", dl_start, dl_end)
            n_dl_1d += 1
            if e1:
                _log("download 1d %s %s" % (c6, e1))
            else:
                _log("download 1d %s" % c6)
            ymds = _local_daily_ymds(ContextInfo, fc, dl_start, dl_end)
        else:
            n_skip_1d += 1
        tick_days = []
        if ymds is not None and _daily_range_covered(ymds, dl_start, dl_end):
            for ymd in ymds:
                if tick_lo <= ymd <= dl_end:
                    tick_days.append(ymd)
        if not tick_days:
            dcur = start_d
            while dcur <= end_d:
                if dcur.weekday() < 5:
                    tick_days.append(dcur.strftime("%Y%m%d"))
                dcur += timedelta(days=1)
        files = _local_tick_ymds(fc)
        action, dl_tick_start = _tick_download_plan(
            tick_days, files, tick_depth_ymd, tick_lo, dl_end
        )
        if action == "download":
            e2 = _call_download(ContextInfo, fc, "tick", dl_tick_start, dl_end)
            n_dl_tick += 1
            if e2:
                _log("download tick %s %s" % (c6, e2))
            else:
                _log("download tick %s" % c6)
        elif action == "skip_old":
            n_skip_tick_old += 1
        else:
            n_skip_tick += 1
        done_dl = i_dl + 1
        if done_dl % 25 == 0 or done_dl == n_dl:
            _log(
                "progress download %d/%d %s skipped_1d=%d downloaded_1d=%d skipped_tick=%d skipped_tick_old=%d downloaded_tick=%d"
                % (done_dl, n_dl, c6, n_skip_1d, n_dl_1d, n_skip_tick, n_skip_tick_old, n_dl_tick)
            )
    _log(
        "cache summary skipped_1d=%d downloaded_1d=%d skipped_tick=%d skipped_tick_old=%d downloaded_tick=%d"
        % (n_skip_1d, n_dl_1d, n_skip_tick, n_skip_tick_old, n_dl_tick)
    )

    daily_cache = {}
    for c6 in codes:
        yield
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
    strength_keep = str(CFG.get("sell_profile") or "") == "strength_keep"
    suppress_gain_clear = bool(CFG.get("suppress_gain_clear")) or strength_keep
    strength_uncond = bool(CFG.get("strength_unconditional")) or strength_keep
    if CFG.get("open_rule_name"):
        NAME_OPEN50 = str(CFG.get("open_rule_name"))
    if CFG.get("lu_rule_name"):
        NAME_LU10 = str(CFG.get("lu_rule_name"))
    strength_clear_name = str(CFG.get("strength_clear_name") or "马总1卖-到期强弱留半或清仓")
    trail_rule_name = str(CFG.get("trail_rule_name") or "马总1卖-持有期最高价回落清仓")
    stop_cost_rule_name = str(CFG.get("stop_cost_rule_name") or "马总1卖-到期后跌破买入价")
    no_new_high_rule_name = str(CFG.get("no_new_high_rule_name") or "马总1卖-连续不创新高清仓")
    try:
        no_new_high_days = int(CFG.get("no_new_high_days") or params.get("no_new_high_days") or 3)
    except Exception:
        no_new_high_days = 3
    if no_new_high_days < 1:
        no_new_high_days = 1
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
        NAME_STOP_BUY = "马总1卖-跌破买入价止损"

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
    if CFG.get("clear_branch_enabled") is None:
        clear_branch_on = bool(is_ind)
    else:
        clear_branch_on = bool(CFG.get("clear_branch_enabled"))
    clear_mode = str(
        CFG.get("scheduled_clear_mode") or params.get("scheduled_clear_mode") or ""
    ).strip()
    try:
        profit_keep_pct = float(CFG.get("profit_keep_pct") if CFG.get("profit_keep_pct") is not None else params.get("profit_keep_pct") or 6.0)
    except Exception:
        profit_keep_pct = 6.0
    try:
        trail_pullback_pct = float(CFG.get("trail_pullback_pct") if CFG.get("trail_pullback_pct") is not None else params.get("trail_pullback_pct") or 5.0)
    except Exception:
        trail_pullback_pct = 5.0

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
        if params.get("stop_loss_from_buy_percent") is not None:
            stop_from_buy_pct = float(params.get("stop_loss_from_buy_percent"))
        else:
            stop_from_buy_pct = 0.0
    except Exception:
        stop_from_buy_pct = 0.0
    if stop_from_buy_pct < 0:
        stop_from_buy_pct = 0.0

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
            yield
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
                try:
                    dh = float((high_by or {}).get(ymd) or 0)
                except Exception:
                    dh = 0.0
                if dh > float(st.get("period_high") or 0):
                    st["period_high"] = dh
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
            di = _sell_day_index(st, day_d, trading_days)
            post_expiry = bool(strength_keep and hold_n is not None and di > int(hold_n))

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
                _bres, sealed, after_sec = (False, False, branch_sec)
                if clear_branch_on:
                    _bres, sealed, after_sec = _resolve_clear_branch(ticks, branch_sec, limit_up)
                if clear_branch_on and not _bres:
                    sealed = False
                elif avail >= 100 and prev_close > 0:
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
                            "seal_open": True,
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
            elif not post_expiry:
                # ---- 马总逻辑1卖：开盘涨幅半仓 + 近涨停半仓。
                # T 点只跟实盘 run()：主板 10%+回落0，或非主板 20%+回落0。
                # 强势留半策略 suppress_gain_clear：不挂涨停即清仓 / 涨幅弹性清仓。 ----
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
                if lu_ref > 0 and _skip_lu_ex_div(
                    yords, close_by, open_by, ymd, prev_close, lr
                ):
                    lu_ref = 0.0

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

                leg_branch = bool(clear_branch_on and _mz1_leg_has_clear_branch(
                    _is_growth_board(c6),
                    thr_clear_main, clear_drop_main,
                    thr_clear_growth, clear_drop_growth,
                ))
                mz_ok, mz_sealed, mz_after = True, False, None
                if leg_branch:
                    mz_ok, mz_sealed, mz_after = _resolve_clear_branch(ticks, branch_sec, limit_up)
                if (not suppress_gain_clear) and avail >= 100 and prev_close > 0 and mz_ok:
                    if leg_branch and mz_sealed and limit_up > 0:
                        _orig_clear = NAME_CLEAR_GROWTH if _is_growth_board(c6) else NAME_CLEAR_LU
                        day_intents.append({
                            "kind": "breakthrough_sell",
                            "name": _orig_clear + "·开板突破卖",
                            "leg": "%s:CLEAR_BRK:%s" % (c6, ymd),
                            "trigger": float(limit_up),
                            "drop": 0.0,
                            "volume": int(avail),
                            "is_clear": True,
                            "after_sec": mz_after,
                            "seal_open": True,
                        })
                    elif _is_growth_board(c6):
                        if thr_clear_growth > 0:
                            ct = _clamp_px(round(prev_close * (1.0 + thr_clear_growth), 2), limit_down, limit_up)
                            if ct > 0:
                                day_intents.append({
                                    "kind": "best_sell", "name": NAME_CLEAR_GROWTH, "leg": "%s:CLEAR_G:%s" % (c6, ymd),
                                    "trigger": float(ct), "drop": clear_drop_growth, "volume": int(avail),
                                    "is_clear": True,
                                    "after_sec": mz_after if leg_branch else None,
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
                                    "after_sec": mz_after if leg_branch else None,
                                })
                            else:
                                day_intents.append({
                                    "kind": "best_sell", "name": NAME_CLEAR_MAIN, "leg": "%s:CLEAR_M:%s" % (c6, ymd),
                                    "trigger": float(ct), "drop": clear_drop_main, "volume": int(avail),
                                    "is_clear": True,
                                    "after_sec": mz_after if leg_branch else None,
                                })

            if (not is_ind) and (not is_surge) and (not post_expiry) and stop_from_buy_pct > 1e-12 and avail >= 100:
                try:
                    _buy_cost = float(st.get("cost") or 0)
                except Exception:
                    _buy_cost = 0.0
                if _buy_cost > 0:
                    stop_px = _clamp_px(
                        round(_buy_cost * (1.0 - float(stop_from_buy_pct) / 100.0), prec),
                        limit_down,
                        limit_up,
                    )
                    if stop_px > 0:
                        day_intents.append({
                            "kind": "breakthrough_sell",
                            "name": NAME_STOP_BUY,
                            "leg": "%s:STOP_BUY:%s" % (c6, ymd),
                            "trigger": float(stop_px),
                            "volume": int(avail),
                        })

            if strength_keep and post_expiry and avail >= 100:
                seed_hp = float(st.get("period_high") or 0)
                day_intents.append({
                    "kind": "best_sell",
                    "name": trail_rule_name,
                    "leg": "%s:TRAIL:%s" % (c6, ymd),
                    "trigger": 0.01,
                    "drop": float(trail_pullback_pct),
                    "volume": int(avail),
                    "seed_high": seed_hp,
                    "is_clear": True,
                })
                try:
                    _cost_px = float(st.get("cost") or 0)
                except Exception:
                    _cost_px = 0.0
                if _cost_px > 0:
                    stop_px = _clamp_px(round(_cost_px, prec), limit_down, limit_up)
                    if stop_px > 0:
                        day_intents.append({
                            "kind": "breakthrough_sell",
                            "name": stop_cost_rule_name,
                            "leg": "%s:STOP_COST:%s" % (c6, ymd),
                            "trigger": float(stop_px),
                            "volume": int(avail),
                            "is_clear": True,
                        })
                hit = _first_scheduled_tick(ticks, clear_sec, 300)
                if hit is not None:
                    hsec, htm, hlast = hit[0], hit[1], hit[2]
                    today_hp = hlast
                    for _t in ticks:
                        if _t[0] > hsec:
                            break
                        if _t[2] > today_hp:
                            today_hp = _t[2]
                        if len(_t) > 3 and _t[3] and _t[3] > today_hp:
                            today_hp = _t[3]
                    hp_now = max(seed_hp, float(today_hp or 0))
                    streak, do_clear = _eval_no_new_high(
                        hp_now, seed_hp, st.get("nh_streak") or 0, no_new_high_days
                    )
                    st["nh_streak"] = streak
                    if do_clear and hlast > 0:
                        day_intents.append({
                            "kind": "scheduled_clear",
                            "name": no_new_high_rule_name,
                            "leg": "%s:NHIGH:%s" % (c6, ymd),
                            "trigger": 99999.0,
                            "drop": 0.0,
                            "volume": int(avail),
                            "force": False,
                            "is_clear": True,
                            "preset_fill": (hsec, htm, hlast),
                        })
            elif hold_n is not None and di == int(hold_n) and avail >= 100:
                clear_px = round(float(limit_up) - float(tick_sz), prec) if limit_up > 0 else 0.0
                force = clear_px <= 0
                nm = "%s-第%d日%s" % (sc_prefix, ui_n, "无条件清仓" if force else "涨停外清仓")
                if strength_keep and strength_uncond:
                    nm = "%s-第%d日" % (strength_clear_name, ui_n)
                    clear_px = 99999.0
                    force = False
                if clear_mode == "strength_half_or_full" and (not force) and clear_px > 0:
                    hit = _first_scheduled_tick(ticks, clear_sec, 300)
                    if hit is not None:
                        hsec, htm, hlast, hhi = hit[0], hit[1], hit[2], hit[3]
                        sess = hlast
                        for _t in ticks:
                            if _t[0] > hsec:
                                break
                            if _t[2] > sess:
                                sess = _t[2]
                            if len(_t) > 3 and _t[3] and _t[3] > sess:
                                sess = _t[3]
                        hp = max(float(st.get("period_high") or 0), float(sess or 0), float(hhi or 0))
                        if hlast + 1e-9 < clear_px:
                            try:
                                _cost = float(st.get("cost") or 0)
                            except Exception:
                                _cost = 0.0
                            cls = _classify_strength(
                                hlast, _cost, hp, profit_keep_pct, trail_pullback_pct
                            )
                            if cls.get("clear_volume_mode") == "half_remaining":
                                vol_sc, _why = _half_remaining_volume(avail, hlast, min_amt)
                                nm_sc = nm if strength_keep else (nm + "·强势留半")
                            else:
                                vol_sc = _full_lot(avail)
                                nm_sc = nm
                            if vol_sc >= 100:
                                day_intents.append({
                                    "kind": "scheduled_clear",
                                    "name": nm_sc,
                                    "leg": "%s:SCLEAR:%s" % (c6, ymd),
                                    "trigger": float(clear_px),
                                    "drop": 0.0,
                                    "volume": int(vol_sc),
                                    "force": False,
                                    "is_clear": True,
                                    "preset_fill": (hsec, htm, hlast),
                                })
                else:
                    day_intents.append({
                        "kind": "scheduled_clear", "name": nm, "leg": "%s:SCLEAR:%s" % (c6, ymd),
                        "trigger": float(clear_px), "drop": 0.0, "volume": int(avail),
                        "force": force, "is_clear": True,
                    })

            # 同一天多条规则先各自找出成交点，再按时间先后扣仓。
            # 挂在前面、但下午才成交的弹性，不能抢走早上已经触发的止损。
            # 同一秒仍按挂出顺序：开盘弹性先于止损。
            planned = []
            for _idx, intent in enumerate(day_intents):
                fill = _quote_intent_fill(
                    intent, ticks, run_start_sec, run_end_sec, prec, clear_sec,
                    limit_up, prev_close,
                )
                if not fill:
                    continue
                sec, tm, px = fill
                if px <= 0:
                    continue
                planned.append((int(sec), _idx, intent, fill))
            planned.sort(key=lambda item: (item[0], item[1]))
            for _sec, _idx, intent, fill in planned:
                hold = _full_lot(int(st.get("volume") or 0))
                avail = _available_qty(st, day_d, from_t1)
                if avail < 100 or hold < 100:
                    break
                vol = min(int(intent.get("volume") or 0), avail, hold)
                vol = _full_lot(vol)
                if vol < 100:
                    continue
                sec, tm, px = fill
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
            day_hi = float(st.get("period_high") or 0)
            for _t in ticks:
                if _t[2] > day_hi:
                    day_hi = _t[2]
                if len(_t) > 3 and _t[3] and _t[3] > day_hi:
                    day_hi = _t[3]
            if day_hi > 0:
                st["period_high"] = day_hi

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
    """One-shot paste entry. The probe drives iter_backtest() and yields between steps."""
    tag = str(CFG.get("run_tag") or "")
    if tag and tag in _DONE_RUN_TAGS:
        _log("SKIP_ALREADY_DONE|%s" % tag)
        return
    try:
        gen = iter_backtest(ContextInfo)
        while True:
            next(gen)
    except StopIteration:
        return
    except Exception as e:
        _emit("ERROR", {"msg": str(e)[:500], "trace": traceback.format_exc()[:1500]})


def init(ContextInfo):
    run_backtest(ContextInfo)


def handlebar(ContextInfo):
    pass
