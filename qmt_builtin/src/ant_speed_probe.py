# -*- coding: utf-8 -*-
"""大 QMT 反应速度探测：行情滞后、回调耗时、513130 真单成交。

请求：data/qmt_speed_probe_request.json（由 tools/qmt_speed_bench_gui.py 写入）
日志：data/qmt_speed_logs/<id>.jsonl
无请求或 cmd=stop 时几乎不做功。真单仅当 confirm_real_order 且数量为 100 或 200。
"""
import json
import os
import time
from datetime import datetime

try:
    from ant_qmt_paths import DATA_DIR
except Exception:
    DATA_DIR = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
    )

_REQ = None
_REQ_MTIME = None
_FIRED_IDS = set()
_SEEN_IDS = set()
_SUMMARY_IDS = set()
_LAST = {"price": 0.0, "ask": 0.0, "bid": 0.0, "code": ""}
_SAMPLE_N = {}
_PRICE_LOGGED = set()
PROBE_REV = 4
TARGET_CODE = "513130.SH"


def _data_dir():
    """请求文件在项目 data 下；QMT 里 import 到的 DATA_DIR 有时不是这一份。"""
    alt = "D:\\" + "\u8682\u8681\u91cf\u5316\u7cfb\u7edf" + "\\data"
    for folder in (DATA_DIR, alt):
        if folder and os.path.isfile(os.path.join(folder, "qmt_speed_probe_request.json")):
            return folder
    return DATA_DIR or alt


def _request_path():
    return os.path.join(_data_dir(), "qmt_speed_probe_request.json")


def _now_ms():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


def _parse_dt(raw):
    s = str(raw or "").strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s[:26], fmt)
        except ValueError:
            continue
    return None


def _load_request():
    global _REQ, _REQ_MTIME
    path = _request_path()
    try:
        mt = os.path.getmtime(path)
    except OSError:
        _REQ = None
        _REQ_MTIME = None
        return None
    if _REQ is not None and mt == _REQ_MTIME:
        return _REQ
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        _REQ = None
        _REQ_MTIME = mt
        return None
    _REQ = data if isinstance(data, dict) else None
    _REQ_MTIME = mt
    return _REQ


def _log(req, event):
    sid = str((req or {}).get("id") or "").strip()
    if not sid:
        return
    folder = os.path.join(_data_dir(), "qmt_speed_logs")
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, sid + ".jsonl")
        event = dict(event)
        event["wall"] = _now_ms()
        event["machine"] = str(req.get("machine") or "")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        print("[测速] 写日志失败: %s" % e)


def _code6(code):
    s = str(code or "").strip().upper()
    if "." in s:
        s = s.split(".", 1)[0]
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits[:6]


def _target6(req):
    return _code6(req.get("code") or TARGET_CODE) or "513130"


def _row_price(row):
    if not isinstance(row, dict):
        return 0.0
    for key in ("lastPrice", "last_price", "price", "last"):
        try:
            v = float(row.get(key) or 0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            return v
    return 0.0


def _row_ask(row):
    if not isinstance(row, dict):
        return 0.0
    raw = row.get("askPrice")
    if raw is None:
        raw = row.get("ask1")
    try:
        if isinstance(raw, (list, tuple)) and raw:
            v = float(raw[0] or 0)
            return v if v > 0 else 0.0
        if raw is not None:
            v = float(raw)
            return v if v > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0
    return 0.0


def _row_bid(row):
    if not isinstance(row, dict):
        return 0.0
    raw = row.get("bidPrice")
    if raw is None:
        raw = row.get("bid1")
    try:
        if isinstance(raw, (list, tuple)) and raw:
            v = float(raw[0] or 0)
            return v if v > 0 else 0.0
        if raw is not None:
            v = float(raw)
            return v if v > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0
    return 0.0


def _row_timetag(row):
    if not isinstance(row, dict):
        return None
    for key in ("timetag", "time", "stime", "timeStamp", "timestamp"):
        v = row.get(key)
        if v is None or str(v).strip() == "":
            continue
        return v
    return None


def _lag_ms(raw_tt):
    """本机现在减行情时间戳，毫秒。只有秒时精度约 1 秒。"""
    if raw_tt is None:
        return None, "none"
    s = str(raw_tt).strip()
    if not s:
        return None, "none"
    digits = "".join(ch for ch in s if ch.isdigit())
    try:
        if s.replace(".", "", 1).isdigit():
            n = float(s)
            if n > 1e12:
                n = n / 1000.0
            if 1e9 < n < 2e10:
                lag = (time.time() - n) * 1000.0
                if -2000 <= lag <= 60000:
                    return round(lag, 1), "unix"
    except (TypeError, ValueError):
        pass
    now = datetime.now()
    clock = None
    if ":" in s:
        part = s.replace("T", " ").split()[-1]
        try:
            hh, mm, ss = part.split(":")[:3]
            clock = now.replace(
                hour=int(hh),
                minute=int(mm),
                second=int(float(ss)),
                microsecond=int((float(ss) % 1) * 1e6),
            )
        except (TypeError, ValueError):
            clock = None
    elif len(digits) >= 14:
        try:
            clock = datetime.strptime(digits[:14], "%Y%m%d%H%M%S")
        except ValueError:
            clock = None
    if clock is None:
        return None, "unparsed"
    lag = (now - clock).total_seconds() * 1000.0
    if lag < -120000 or lag > 120000:
        return None, "out_of_range"
    prec = "sec" if clock.microsecond == 0 else "subsec"
    return round(max(0.0, lag), 1), prec


def _quote_active(req):
    if not isinstance(req, dict) or str(req.get("cmd") or "") != "quote":
        return False
    started = _parse_dt(req.get("started_at"))
    if started is None:
        return False
    try:
        dur = float(req.get("duration_sec") or 20)
    except (TypeError, ValueError):
        dur = 20.0
    return 0 <= (datetime.now() - started).total_seconds() <= max(1.0, dur)


def _order_ready(req):
    if not isinstance(req, dict) or str(req.get("cmd") or "") != "order":
        return False
    if not bool(req.get("confirm_real_order")):
        return False
    try:
        vol = int(req.get("volume") or 0)
    except (TypeError, ValueError):
        return False
    if vol not in (100, 200):
        return False
    if _target6(req) != "513130":
        return False
    sid = str(req.get("id") or "")
    if not sid or sid in _FIRED_IDS or _already_claimed(sid):
        return False
    fire = _parse_dt(req.get("fire_at"))
    if fire is None:
        return False
    return datetime.now() >= fire


def _remember_price(code, row):
    px = _row_price(row)
    ask = _row_ask(row)
    bid = _row_bid(row)
    if px > 0 or ask > 0 or bid > 0:
        _LAST["code"] = _code6(code)
        if px > 0:
            _LAST["price"] = px
        if ask > 0:
            _LAST["ask"] = ask
        if bid > 0:
            _LAST["bid"] = bid


def _aggressive_buy_price(last, ask):
    base = ask if ask > 0 else last
    if base <= 0:
        return 0.0
    # 略高于卖一/最新价，方便尽快成交；ETF 价格 3 位小数
    px = base * 1.003
    cap = (last if last > 0 else base) * 1.02
    if cap > 0:
        px = min(px, cap)
    return round(px + 1e-9, 3)


def _aggressive_sell_price(last, bid):
    base = bid if bid > 0 else last
    if base <= 0:
        return 0.0
    px = base * 0.997
    floor = (last if last > 0 else base) * 0.98
    if floor > 0:
        px = max(px, floor)
    return round(px + 1e-9, 3)


def _claim_path(sid):
    return os.path.join(_data_dir(), "qmt_speed_logs", sid + ".fired")


def _already_claimed(sid):
    return bool(sid) and os.path.isfile(_claim_path(sid))


def _claim_fire(sid):
    """跨模块只发一次。失败且尚未调用 passorder 时删锁，允许重试。"""
    folder = os.path.join(_data_dir(), "qmt_speed_logs")
    try:
        os.makedirs(folder, exist_ok=True)
        fd = os.open(_claim_path(sid), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(fd)
        return True
    except OSError:
        return False


def _release_claim(sid):
    try:
        os.remove(_claim_path(sid))
    except OSError:
        pass


def _passorder_mod():
    """用进程里已经绑定过的 passorder，不要 import 出一份没绑定的新模块。"""
    import importlib.util
    import sys

    donors = []
    fn = None
    for name, mod in list(sys.modules.items()):
        if name == "ant_passorder" or str(name).startswith("ant_passorder_"):
            donors.append(mod)
            cand = getattr(mod, "_PASSORDER", None)
            if callable(cand):
                fn = cand
                break
    if not callable(fn):
        try:
            import builtins

            fn = getattr(builtins, "passorder", None)
        except Exception:
            fn = None
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ant_passorder.py")
    if not os.path.isfile(path):
        return None
    mod_name = "ant_passorder_speed_%d" % int(os.path.getmtime(path))
    mod = sys.modules.get(mod_name)
    if mod is None:
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            return None
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)
    if callable(fn):
        try:
            mod._PASSORDER = fn
        except Exception:
            pass
        try:
            import builtins

            builtins.passorder = fn
        except Exception:
            pass
    elif hasattr(mod, "bind_runtime_globals"):
        try:
            mod.bind_runtime_globals(None)
        except Exception:
            pass
    return mod if hasattr(mod, "place_market_order") else None


def _find_context():
    import sys

    fallback = None
    for mod in list(sys.modules.values()):
        if mod is None:
            continue
        ctx = getattr(mod, "_CONTEXT", None)
        if ctx is None:
            continue
        if hasattr(mod, "reload_rules_if_changed"):
            return ctx
        fallback = ctx
    return fallback


def from_runner():
    """已在跑的 tick_runner 热加载后调用，不依赖策略重启。"""
    ctx = _find_context()
    if ctx is None:
        return
    on_periodic(ctx)


def _fire_order(ContextInfo, req, source):
    sid = str(req.get("id") or "")
    if not sid or sid in _FIRED_IDS or _already_claimed(sid):
        if sid:
            _FIRED_IDS.add(sid)
        return
    side = str(req.get("side") or "buy").strip().lower()
    if side not in ("buy", "sell"):
        side = "buy"
    vol = int(req.get("volume") or 0)
    po = _passorder_mod()
    if po is None:
        _log(req, {"kind": "order_error", "msg": "no_passorder", "side": side})
        return
    _fill_price(ContextInfo)
    if side == "sell":
        px = _aggressive_sell_price(float(_LAST.get("price") or 0), float(_LAST.get("bid") or 0))
        place = getattr(po, "place_limit_sell", None)
    else:
        px = _aggressive_buy_price(float(_LAST.get("price") or 0), float(_LAST.get("ask") or 0))
        place = getattr(po, "place_limit_buy", None)
    if px <= 0 or not callable(place):
        if sid not in _PRICE_LOGGED:
            _PRICE_LOGGED.add(sid)
            _log(req, {"kind": "order_error", "msg": "no_price", "side": side, "px": px})
        return
    if not _claim_fire(sid):
        _FIRED_IDS.add(sid)
        return
    _FIRED_IDS.add(sid)
    name = "测速513130卖" if side == "sell" else "测速513130买"
    uid = ("spd" + sid)[-32:]
    t0 = time.perf_counter()
    ok, reason, record = place(
        ContextInfo,
        "513130.SH",
        px,
        vol,
        strategy_name=name,
        user_order_id=uid,
        quick_trade=2,
    )
    if not ok and reason in ("passorder_unbound", "no_account_id", "no_context", "bad_params"):
        _FIRED_IDS.discard(sid)
        _release_claim(sid)
    call_us = round((time.perf_counter() - t0) * 1e6, 1)
    _log(
        req,
        {
            "kind": "order_sent",
            "side": side,
            "price_type": "limit",
            "price": (record or {}).get("price"),
            "ok": bool(ok),
            "reason": reason,
            "volume": vol,
            "call_us": call_us,
            "source": source,
            "pass_uid": (record or {}).get("pass_uid") or "",
        },
    )
    print(
        "[测速] 513130 限价%s vol=%s px=%s ok=%s reason=%s"
        % ("卖出" if side == "sell" else "买入", vol, (record or {}).get("price"), ok, reason)
    )


def _maybe_summary(req):
    sid = str((req or {}).get("id") or "")
    if not sid or sid in _SUMMARY_IDS:
        return
    if str(req.get("cmd") or "") != "quote":
        return
    started = _parse_dt(req.get("started_at"))
    if started is None:
        return
    try:
        dur = float(req.get("duration_sec") or 20)
    except (TypeError, ValueError):
        dur = 20.0
    # 预约开始之前不要记结束；只在采样窗口过去之后写一次
    if (datetime.now() - started).total_seconds() < max(1.0, dur):
        return
    _SUMMARY_IDS.add(sid)
    _log(req, {"kind": "quote_done", "samples": int(_SAMPLE_N.get(sid) or 0)})


def on_strategy_tick(ContextInfo, datas, strategy_us):
    """主策略 _on_tick 结束时调用。strategy_us 为整段回调耗时。"""
    req = _load_request()
    if not req or not isinstance(datas, dict):
        return
    _maybe_summary(req)
    want = _target6(req)
    hit_row = None
    for stock_code, stock_data in datas.items():
        if _code6(stock_code) != want:
            continue
        if isinstance(stock_data, dict):
            hit_row = stock_data
            _remember_price(stock_code, stock_data)
            break
    if hit_row is not None and _quote_active(req):
        sid = str(req.get("id") or "")
        n = int(_SAMPLE_N.get(sid) or 0)
        if n < 4000:
            raw_tt = _row_timetag(hit_row)
            lag, prec = _lag_ms(raw_tt)
            _SAMPLE_N[sid] = n + 1
            _log(
                req,
                {
                    "kind": "quote",
                    "code": "513130.SH",
                    "last": _row_price(hit_row),
                    "ask": _row_ask(hit_row),
                    "timetag": "" if raw_tt is None else str(raw_tt),
                    "lag_ms": lag,
                    "lag_prec": prec,
                    "strategy_us": round(float(strategy_us or 0), 1),
                },
            )
    if hit_row is not None and _order_ready(req):
        _fire_order(ContextInfo, req, "tick")
    elif _order_ready(req) and _LAST.get("code") == "513130":
        _fire_order(ContextInfo, req, "tick_cached")


def on_periodic(ContextInfo):
    if not getattr(on_periodic, "_booted", False):
        on_periodic._booted = True
        print("[测速] 限价通道已加载")
    req = _load_request()
    if not req:
        return
    _maybe_summary(req)
    sid = str(req.get("id") or "")
    if str(req.get("cmd") or "") == "order" and sid and sid not in _SEEN_IDS:
        _SEEN_IDS.add(sid)
        _log(req, {"kind": "probe_seen", "fire_at": str(req.get("fire_at") or "")})
    if not _order_ready(req):
        return
    _fire_order(ContextInfo, req, "periodic")


def _row_from_obj(raw):
    if isinstance(raw, dict):
        return raw
    if raw is None:
        return None
    out = {}
    for key in ("lastPrice", "last_price", "price", "askPrice", "bidPrice"):
        try:
            val = getattr(raw, key, None)
        except Exception:
            val = None
        if val is not None:
            out[key] = val
    return out or None


def _fill_price(ContextInfo):
    code = TARGET_CODE

    def _take(tick_map):
        if not isinstance(tick_map, dict):
            return False
        row = tick_map.get(code) or tick_map.get("513130")
        if not isinstance(row, dict):
            row = _row_from_obj(row)
        if isinstance(row, dict):
            _remember_price(code, row)
            return float(_LAST.get("price") or 0) > 0
        return False

    try:
        fn = getattr(ContextInfo, "get_full_tick", None)
        if callable(fn) and _take(fn([code])):
            return
    except Exception:
        pass
    if float(_LAST.get("price") or 0) > 0 and _LAST.get("code") == "513130":
        return
    try:
        import xtquant.xtdata as xtdata

        try:
            xtdata.enable_hello = False
        except Exception:
            pass
        if _take(xtdata.get_full_tick([code])):
            return
    except Exception:
        pass
    path = os.path.join(_data_dir(), "results.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    stocks = data.get("stocks") if isinstance(data, dict) else None
    if not isinstance(stocks, dict):
        return
    for key, bucket in stocks.items():
        if _code6(key) != "513130" or not isinstance(bucket, dict):
            continue
        try:
            px = float(bucket.get("last_price") or 0)
        except (TypeError, ValueError):
            px = 0.0
        if px > 0:
            _LAST["code"] = "513130"
            _LAST["price"] = px
            break


def _deal_code_volume(dealInfo):
    code = ""
    vol = 0
    trade_time = ""
    price = 0.0
    if isinstance(dealInfo, dict):
        code = str(dealInfo.get("stock_code") or dealInfo.get("m_strInstrumentID") or "")
        vol = dealInfo.get("volume") or dealInfo.get("m_nVolume") or 0
        trade_time = str(dealInfo.get("m_strTradeTime") or dealInfo.get("trade_time") or "")
        price = dealInfo.get("price") or dealInfo.get("m_dPrice") or 0
    else:
        for attr in ("m_strInstrumentID", "stock_code", "m_strCode"):
            v = getattr(dealInfo, attr, None)
            if v:
                code = str(v)
                break
        for attr in ("m_nVolume", "volume", "m_nTradeVolume"):
            v = getattr(dealInfo, attr, None)
            if v:
                vol = v
                break
        trade_time = str(getattr(dealInfo, "m_strTradeTime", "") or "")
        price = getattr(dealInfo, "m_dPrice", 0) or getattr(dealInfo, "price", 0)
    try:
        vol = int(float(vol or 0))
    except (TypeError, ValueError):
        vol = 0
    try:
        price = float(price or 0)
    except (TypeError, ValueError):
        price = 0.0
    return code, vol, trade_time, price


def on_deal(ContextInfo, dealInfo):
    req = _load_request()
    if not req or str(req.get("cmd") or "") not in ("order", "stop"):
        return
    code, vol, trade_time, price = _deal_code_volume(dealInfo)
    if _code6(code) != "513130":
        return
    sid = str(req.get("id") or "")
    if sid not in _FIRED_IDS:
        return
    _log(
        req,
        {
            "kind": "fill",
            "code": "513130.SH",
            "volume": vol,
            "price": price,
            "trade_time": trade_time,
        },
    )
    print("[测速] 513130 成交 vol=%s px=%s trade_time=%s" % (vol, price, trade_time))


def on_order(ContextInfo, orderInfo):
    req = _load_request()
    if not req:
        return
    code = ""
    status = ""
    if isinstance(orderInfo, dict):
        code = str(orderInfo.get("stock_code") or orderInfo.get("m_strInstrumentID") or "")
        status = str(orderInfo.get("order_status") or orderInfo.get("m_nOrderStatus") or "")
    else:
        code = str(
            getattr(orderInfo, "m_strInstrumentID", "")
            or getattr(orderInfo, "stock_code", "")
            or ""
        )
        status = str(getattr(orderInfo, "m_nOrderStatus", "") or "")
    if _code6(code) != "513130":
        return
    sid = str(req.get("id") or "")
    if sid not in _FIRED_IDS:
        return
    _log(req, {"kind": "order_status", "status": status})
