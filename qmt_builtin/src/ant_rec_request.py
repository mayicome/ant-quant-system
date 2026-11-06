# -*- coding: utf-8 -*-
"""QMT side: one recommendation job (sell+buy) at a time.

Pending list lives in ant_rec_local.py (hot-reload by mtime).
Do not write project disks; emit ANTRES rec only.
Heavy work only via process_pending_recs from periodic sync — never handlebar.
"""
import json
import math
import os
import sys
from datetime import datetime

_REC_MTIME = 0.0
_REC_MOD = None
_DONE = {}
_BUSY = False

# 0.0% .. 3.0% step 0.1
_PCT_RANGE = [round(x * 0.1, 1) for x in range(0, 31)]


def _now_iso():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _local_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "ant_rec_local.py")


def _load_local():
    global _REC_MTIME, _REC_MOD
    path = _local_path()
    if not os.path.isfile(path):
        return None
    try:
        mtime = float(os.path.getmtime(path))
    except OSError:
        return None
    if _REC_MOD is not None and mtime == _REC_MTIME:
        return _REC_MOD
    import importlib.util

    mod_name = "ant_rec_local_%d" % int(mtime)
    existing = sys.modules.get(mod_name)
    if existing is not None:
        _REC_MOD = existing
        _REC_MTIME = mtime
        return existing
    spec = importlib.util.spec_from_file_location(mod_name, path)
    if spec is None or spec.loader is None:
        return None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    _REC_MOD = mod
    _REC_MTIME = mtime
    return mod


def _pending_rows():
    mod = _load_local()
    if mod is None:
        return []
    raw = getattr(mod, "RECS", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    if not isinstance(raw, list):
        return []
    out = []
    for it in raw:
        if isinstance(it, dict):
            out.append(it)
    return out


def _to_float(x):
    if x is None:
        return None
    if isinstance(x, (list, tuple)):
        if not x:
            return None
        return _to_float(x[0])
    if hasattr(x, "__len__") and hasattr(x, "__getitem__") and not isinstance(x, (str, bytes, dict)):
        try:
            if len(x) > 0:
                return _to_float(x[0])
        except Exception:
            pass
    try:
        return float(x)
    except Exception:
        return None


def _normalize_df(raw_df):
    if raw_df is None:
        return None
    try:
        import ant_tick_cache_io as tio
    except ImportError:
        try:
            import qmt_builtin.ant_tick_cache_io as tio
        except ImportError:
            tio = None
    if tio is not None:
        try:
            if hasattr(tio, "flatten_depth_columns"):
                raw_df = tio.flatten_depth_columns(raw_df)
            if hasattr(tio, "normalize_tick_dataframe"):
                return tio.normalize_tick_dataframe(raw_df)
            if hasattr(tio, "coerce_tick_dataframe"):
                return tio.coerce_tick_dataframe(raw_df)
        except Exception:
            pass
    return raw_df


def _fetch_tick_df(code6, day, ContextInfo=None):
    try:
        import ant_tick_cache_io as tio
    except ImportError:
        try:
            import qmt_builtin.ant_tick_cache_io as tio
        except ImportError:
            tio = None
    if tio is not None and hasattr(tio, "fetch_tick_from_qmt"):
        try:
            raw = tio.fetch_tick_from_qmt(
                code6,
                day,
                ContextInfo=ContextInfo,
                allow_xtdata_download=False,
                allow_subscribe=False,
                allow_builtin_download=True,
            )
            return _normalize_df(raw)
        except Exception as exc:
            print("[rec] fetch_tick_from_qmt err: %s" % exc)
    if ContextInfo is None:
        return None
    full = code6
    if "." not in code6:
        if code6.startswith(("5", "6", "9")):
            full = code6 + ".SH"
        elif code6.startswith(("4", "8")):
            full = code6 + ".BJ"
        else:
            full = code6 + ".SZ"
    start = day + "093000"
    end = day + "150100"
    for fn_name in ("get_local_data", "get_market_data_ex"):
        fn = getattr(ContextInfo, fn_name, None)
        if not callable(fn):
            continue
        try:
            data = fn(
                field=[],
                stock_code=[full],
                period="tick",
                start_time=start,
                end_time=end,
                count=-1,
                dividend_type="none",
                fill_data=False,
                subscribe=False,
            )
        except TypeError:
            try:
                data = fn([full], "tick", start, end)
            except Exception:
                continue
        except Exception:
            continue
        if isinstance(data, dict):
            raw = data.get(full) or data.get(code6)
        else:
            raw = data
        if raw is not None and not getattr(raw, "empty", True):
            return _normalize_df(raw)
    return None


def _prices_from_df(df):
    """Extract lastPrice float list; drop non-positive."""
    if df is None or getattr(df, "empty", True):
        return []
    cols = set(str(c) for c in list(getattr(df, "columns", [])))
    price_col = None
    for name in ("lastPrice", "last", "price", "close"):
        if name in cols:
            price_col = name
            break
    if price_col is None:
        return []
    out = []
    try:
        for v in df[price_col]:
            fv = _to_float(v)
            if fv is None or fv <= 0:
                continue
            out.append(fv)
    except Exception:
        return []
    return out


def _sim_sell_all(prices, start_index, drop_range):
    n = len(prices)
    if start_index >= n or start_index < 0:
        return None
    trigger = prices[start_index]
    if trigger <= 0:
        return None
    highest = trigger
    sold = {dp: False for dp in drop_range}
    sell_px = {dp: None for dp in drop_range}
    high_at_sell = {dp: trigger for dp in drop_range}
    for i in range(start_index + 1, n):
        px = prices[i]
        if px > highest:
            highest = px
        if highest <= trigger:
            continue
        for dp in drop_range:
            if sold[dp]:
                continue
            target = highest * (1.0 - dp / 100.0)
            if px <= target and px < highest:
                sold[dp] = True
                sell_px[dp] = px
                high_at_sell[dp] = highest
        # keep highest for unsold
        for dp in drop_range:
            if not sold[dp]:
                high_at_sell[dp] = highest
    out = {}
    for dp in drop_range:
        hp = high_at_sell[dp]
        if sold[dp] and sell_px[dp] is not None:
            actual = (sell_px[dp] - trigger) / trigger * 100.0
            if sell_px[dp] >= trigger:
                dd = 0.0
            else:
                dd = (trigger - sell_px[dp]) / trigger * 100.0
        else:
            actual = 0.0
            dd = 0.0
        out[dp] = {
            "sold": sold[dp],
            "actual_profit": actual,
            "drawdown": dd,
        }
    return out


def _sim_buy_all(prices, start_index, rise_range):
    n = len(prices)
    if start_index >= n or start_index < 0:
        return None
    trigger = prices[start_index]
    if trigger <= 0:
        return None
    lowest = trigger
    bought = {rp: False for rp in rise_range}
    buy_px = {rp: None for rp in rise_range}
    low_at_buy = {rp: trigger for rp in rise_range}
    for i in range(start_index + 1, n):
        px = prices[i]
        if px < lowest:
            lowest = px
        if lowest >= trigger:
            continue
        for rp in rise_range:
            if bought[rp]:
                continue
            target = lowest * (1.0 + rp / 100.0)
            if px >= target and px > lowest:
                bought[rp] = True
                buy_px[rp] = px
                low_at_buy[rp] = lowest
        for rp in rise_range:
            if not bought[rp]:
                low_at_buy[rp] = lowest
    out = {}
    for rp in rise_range:
        if bought[rp] and buy_px[rp] is not None:
            actual = (trigger - buy_px[rp]) / trigger * 100.0
            if buy_px[rp] <= trigger:
                dd = 0.0
            else:
                dd = (buy_px[rp] - trigger) / trigger * 100.0
        else:
            actual = 0.0
            dd = 0.0
        out[rp] = {
            "bought": bought[rp],
            "actual_saving": actual,
            "drawdown": dd,
        }
    return out


def _aggregate_sell(day_price_lists, sample_interval):
    pct_range = list(_PCT_RANGE)
    stats = {
        dp: {
            "sold_count": 0,
            "total_samples": 0,
            "total_actual_profit": 0.0,
            "max_drawdown": 0.0,
        }
        for dp in pct_range
    }
    for prices in day_price_lists:
        if not prices:
            continue
        si = max(1, int(sample_interval or 30))
        for start in range(0, len(prices), si):
            comb = _sim_sell_all(prices, start, pct_range)
            if not comb:
                continue
            for dp in pct_range:
                row = comb[dp]
                stats[dp]["total_samples"] += 1
                if row["sold"]:
                    stats[dp]["sold_count"] += 1
                    stats[dp]["total_actual_profit"] += float(row["actual_profit"])
                dd = float(row.get("drawdown") or 0)
                if dd > stats[dp]["max_drawdown"]:
                    stats[dp]["max_drawdown"] = dd
    results = {}
    for dp in pct_range:
        s = stats[dp]
        sold_n = s["sold_count"]
        tot = s["total_samples"]
        results[dp] = {
            "sell_rate": (sold_n / tot) if tot > 0 else 0.0,
            "avg_actual_profit": (s["total_actual_profit"] / sold_n) if sold_n > 0 else 0.0,
            "max_drawdown": s["max_drawdown"],
            "sold_count": sold_n,
            "total_samples": tot,
        }
    return results


def _aggregate_buy(day_price_lists, sample_interval):
    pct_range = list(_PCT_RANGE)
    stats = {
        rp: {
            "bought_count": 0,
            "total_samples": 0,
            "total_actual_saving": 0.0,
            "max_drawdown": 0.0,
        }
        for rp in pct_range
    }
    for prices in day_price_lists:
        if not prices:
            continue
        si = max(1, int(sample_interval or 30))
        for start in range(0, len(prices), si):
            comb = _sim_buy_all(prices, start, pct_range)
            if not comb:
                continue
            for rp in pct_range:
                row = comb[rp]
                stats[rp]["total_samples"] += 1
                if row["bought"]:
                    stats[rp]["bought_count"] += 1
                    stats[rp]["total_actual_saving"] += float(row["actual_saving"])
                dd = float(row.get("drawdown") or 0)
                if dd > stats[rp]["max_drawdown"]:
                    stats[rp]["max_drawdown"] = dd
    results = {}
    for rp in pct_range:
        s = stats[rp]
        bought_n = s["bought_count"]
        tot = s["total_samples"]
        results[rp] = {
            "buy_rate": (bought_n / tot) if tot > 0 else 0.0,
            "avg_actual_saving": (s["total_actual_saving"] / bought_n) if bought_n > 0 else 0.0,
            "max_drawdown": s["max_drawdown"],
            "bought_count": bought_n,
            "total_samples": tot,
        }
    return results


def _pick_sell_strategies(results):
    if not results:
        return None
    sorted1 = sorted(results.items(), key=lambda x: x[1]["avg_actual_profit"], reverse=True)
    s1, s1d = sorted1[0]
    target = 0.75
    above = [(dp, r) for dp, r in results.items() if r["sell_rate"] >= target]
    if above:
        s2, s2d = max(above, key=lambda x: x[1]["avg_actual_profit"])
    else:
        s2, s2d = min(results.items(), key=lambda x: abs(x[1]["sell_rate"] - target))
    return {
        "s1": float(s1),
        "s1r": float(s1d["sell_rate"]) * 100.0,
        "s1p": float(s1d["avg_actual_profit"]),
        "s1d": float(s1d["max_drawdown"]),
        "s2": float(s2),
        "s2r": float(s2d["sell_rate"]) * 100.0,
        "s2p": float(s2d["avg_actual_profit"]),
        "s2d": float(s2d["max_drawdown"]),
        "n": int(s1d.get("total_samples") or 0),
    }


def _pick_buy_strategies(results):
    if not results:
        return None
    sorted1 = sorted(results.items(), key=lambda x: x[1]["avg_actual_saving"], reverse=True)
    b1, b1d = sorted1[0]
    target = 0.75
    above = [(rp, r) for rp, r in results.items() if r["buy_rate"] >= target]
    if above:
        b2, b2d = max(above, key=lambda x: x[1]["avg_actual_saving"])
    else:
        b2, b2d = min(results.items(), key=lambda x: abs(x[1]["buy_rate"] - target))
    return {
        "b1": float(b1),
        "b1r": float(b1d["buy_rate"]) * 100.0,
        "b1s": float(b1d["avg_actual_saving"]),
        "b1d": float(b1d["max_drawdown"]),
        "b2": float(b2),
        "b2r": float(b2d["buy_rate"]) * 100.0,
        "b2s": float(b2d["avg_actual_saving"]),
        "b2d": float(b2d["max_drawdown"]),
    }


def _round_or_none(v, nd=2):
    if v is None:
        return None
    try:
        fv = float(v)
    except Exception:
        return None
    if not math.isfinite(fv):
        return None
    return round(fv, nd)


def _emit(row):
    try:
        import ant_result_log as rl
    except ImportError:
        try:
            import qmt_builtin.ant_result_log as rl
        except ImportError:
            rl = None
    if rl is not None and hasattr(rl, "emit_rec"):
        try:
            rl.emit_rec(row)
            return
        except Exception:
            pass
    try:
        print("ANTRES rec " + json.dumps(row, ensure_ascii=False, separators=(",", ":")))
    except Exception:
        pass


def analyze_one(code6, days, sample_interval=30, ContextInfo=None, fake=False):
    """Return metrics dict (no id/st). days: list of YYYYMMDD strings."""
    if fake:
        return {
            "ok": True,
            "msg": "fake",
            "nd": len(days or []),
            "n": 99,
            "s1": 0.8,
            "s1r": 42.0,
            "s1p": 1.25,
            "s1d": 0.40,
            "s2": 0.3,
            "s2r": 78.0,
            "s2p": 0.90,
            "s2d": 0.20,
            "b1": 0.7,
            "b1r": 48.0,
            "b1s": 1.10,
            "b1d": 0.30,
            "b2": 0.4,
            "b2r": 76.0,
            "b2s": 0.85,
            "b2d": 0.15,
        }
    day_lists = []
    for day in days or []:
        d = str(day or "").replace("-", "").replace("/", "")[:8]
        if len(d) != 8:
            continue
        df = _fetch_tick_df(code6, d, ContextInfo=ContextInfo)
        prices = _prices_from_df(df)
        if prices:
            day_lists.append(prices)
    if not day_lists:
        return {"ok": False, "msg": "no_tick", "n": 0, "nd": 0}
    sell_res = _aggregate_sell(day_lists, sample_interval)
    buy_res = _aggregate_buy(day_lists, sample_interval)
    sell_pick = _pick_sell_strategies(sell_res)
    buy_pick = _pick_buy_strategies(buy_res)
    if not sell_pick or not buy_pick:
        return {"ok": False, "msg": "no_stats", "n": 0, "nd": len(day_lists)}
    out = {"ok": True, "msg": "", "nd": len(day_lists)}
    for k, v in sell_pick.items():
        out[k] = v
    for k, v in buy_pick.items():
        out[k] = v
    # compact rounding for log
    for k in ("s1", "s2", "b1", "b2"):
        if k in out:
            out[k] = _round_or_none(out[k], 1)
    for k in ("s1r", "s1p", "s1d", "s2r", "s2p", "s2d", "b1r", "b1s", "b1d", "b2r", "b2s", "b2d"):
        if k in out:
            out[k] = _round_or_none(out[k], 2)
    return out


def process_pending_recs(ContextInfo=None, max_items=1):
    """Process at most max_items pending rec jobs. Safe under live-only.

    Explicitly allowed on live-only machines: on-demand one-stock analysis,
    not the paused full-A tick dump. Does not write project disks.
    Must only be called from periodic sync — never handlebar.
    """
    global _BUSY
    if _BUSY:
        return 0
    rows = _pending_rows()
    if not rows:
        return 0
    done_n = 0
    limit = max(1, int(max_items or 1))
    _BUSY = True
    try:
        for it in rows:
            if done_n >= limit:
                break
            rid = str(it.get("id") or "").strip()
            code = str(it.get("c") or "").strip().upper()
            if code.endswith((".SH", ".SZ", ".BJ")):
                code = code.split(".", 1)[0]
            code = "".join(ch for ch in code if ch.isdigit())[:6]
            if code:
                code = code.zfill(6)
            raw_days = it.get("days") or it.get("ds") or []
            days = []
            if isinstance(raw_days, (list, tuple)):
                for d in raw_days:
                    dd = str(d or "").replace("-", "").replace("/", "")[:8]
                    if len(dd) == 8:
                        days.append(dd)
            try:
                si = int(it.get("si") or it.get("sample_interval") or 30)
            except Exception:
                si = 30
            fake = bool(it.get("fake") or it.get("fk"))
            if not rid:
                continue
            if _DONE.get(rid):
                continue
            at = _now_iso()
            if len(code) != 6 or (not days and not fake):
                _DONE[rid] = "error"
                _emit({"id": rid[:16], "c": code, "st": "error", "msg": "bad_args", "at": at})
                done_n += 1
                continue
            try:
                metrics = analyze_one(code, days, sample_interval=si, ContextInfo=ContextInfo, fake=fake)
            except Exception as exc:
                metrics = {"ok": False, "msg": "%s:%s" % (type(exc).__name__, exc), "n": 0}
            row = {"id": rid[:16], "c": code, "at": at}
            if metrics.get("ok"):
                row["st"] = "ok"
                for k in (
                    "s1", "s1r", "s1p", "s1d",
                    "s2", "s2r", "s2p", "s2d",
                    "b1", "b1r", "b1s", "b1d",
                    "b2", "b2r", "b2s", "b2d",
                    "n", "nd",
                ):
                    if k in metrics and metrics.get(k) is not None and metrics.get(k) != "":
                        row[k] = metrics.get(k)
                if metrics.get("msg"):
                    row["msg"] = str(metrics.get("msg"))[:20]
            else:
                row["st"] = "error"
                row["msg"] = str(metrics.get("msg") or "fail")[:80]
                if metrics.get("n") is not None:
                    row["n"] = int(metrics.get("n") or 0)
                if metrics.get("nd") is not None:
                    row["nd"] = int(metrics.get("nd") or 0)
            _DONE[rid] = row["st"]
            _emit(row)
            done_n += 1
            print("[rec] %s %s st=%s n=%s nd=%s" % (code, rid[:8], row.get("st"), row.get("n"), row.get("nd")))
    finally:
        _BUSY = False
    return done_n
